"""Read-only LUCAS census adapter for locally cached pinned source snapshots."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

import shapely
from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from tools import lucas_source_pool_census as census


def _validate_snapshot_root(path: Path, repository: str, revision: str) -> Path:
    """Require an exact HF Hub cache snapshot; this reader never downloads."""
    root = path.expanduser()
    cache_name = f"datasets--{repository.replace('/', '--')}"
    if (
        not root.is_dir()
        or root.name != revision
        or root.parent.name != "snapshots"
        or root.parent.parent.name != cache_name
    ):
        raise ValueError(
            f"snapshot must be {cache_name}/snapshots/{revision} for the pinned repo"
        )
    return root


def _snapshot_file(root: Path, template: str, *, region: str, stream: str) -> Path:
    relative = PurePosixPath(template.format(region=region, stream=stream))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("direct-source path template must stay inside its snapshot")
    path = root.joinpath(*relative.parts)
    if not path.is_file():
        raise FileNotFoundError(f"pinned source snapshot file is missing: {relative}")
    return path


def _parquet_row_count(path: Path) -> int:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "install PyArrow to read pinned source Parquet files"
        ) from exc
    return pq.ParquetFile(path).metadata.num_rows


def _osm_polygon_id(row: dict[str, Any]) -> str:
    osm_type = row.get("osm_type")
    osm_id = row.get("osm_id")
    if not isinstance(osm_type, str) or not osm_type:
        raise ValueError("Description polygon row has an invalid osm_type")
    if isinstance(osm_id, bool) or not isinstance(osm_id, int | str) or not str(osm_id):
        raise ValueError("Description polygon row has an invalid osm_id")
    return f"osm:{osm_type}/{osm_id}"


def _polygon_id(row: dict[str, Any], schema: str) -> str:
    if schema == "description-language-v1":
        return _osm_polygon_id(row)
    value = row.get("polygon_id")
    if not isinstance(value, str) or not value:
        raise ValueError("source polygon row has a missing polygon_id")
    return value


def _project_geometry(
    value: Any, encoding: str, transformer: Any
) -> tuple[BaseGeometry | None, str | None]:
    try:
        geometry = census._decode_geometry(value, encoding)
    except (ShapelyError, TypeError, ValueError):
        return None, "invalid_wkb" if encoding == "wkb" else "invalid_geojson"
    if not isinstance(geometry, Polygon | MultiPolygon):
        return None, "non_polygon_geometry"
    minx, miny, maxx, maxy = geometry.bounds
    if not all(map(math.isfinite, geometry.bounds)) or (
        minx < -180 or miny < -90 or maxx > 180 or maxy > 90
    ):
        return None, "outside_crs84_bounds"
    if not geometry.is_valid:
        return None, "invalid_geometry"
    if geometry.area <= 0:
        return None, "zero_area"
    projected = shapely.transform(geometry, transformer.transform, interleaved=False)
    if not isinstance(projected, Polygon | MultiPolygon) or not projected.is_valid:
        return None, "invalid_after_projection"
    if projected.area <= 0:
        return None, "zero_area_after_projection"
    return projected, None


def _load_direct_polygons(
    path: Path,
    schema: str,
    transformer: Any,
    encoding: str,
) -> tuple[
    list[tuple[str, BaseGeometry]],
    set[str],
    Counter[str],
    dict[str, str],
    int,
]:
    columns = (
        ("osm_type", "osm_id", "geometry")
        if schema == "description-language-v1"
        else ("polygon_id", "geometry")
    )
    geometries: list[tuple[str, BaseGeometry]] = []
    polygon_ids: set[str] = set()
    exclusions: Counter[str] = Counter()
    decisions: dict[str, str] = {}
    row_count = 0
    for row in census._iter_parquet_rows(path, columns):
        row_count += 1
        polygon_id = _polygon_id(row, schema)
        if polygon_id in polygon_ids:
            raise ValueError(f"duplicate source polygon identifier {polygon_id!r}")
        polygon_ids.add(polygon_id)
        geometry, exclusion = _project_geometry(
            row.get("geometry"), encoding, transformer
        )
        if exclusion is not None:
            exclusions[exclusion] += 1
            decisions[polygon_id] = exclusion
        else:
            assert geometry is not None
            geometries.append((polygon_id, geometry))
    if row_count != _parquet_row_count(path):
        raise ValueError("source polygon row count differs from Parquet metadata")
    return geometries, polygon_ids, exclusions, decisions, row_count


def _sentence_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _description_text_units(
    path: Path, source_polygon_ids: set[str]
) -> Iterator[dict[str, Any]]:
    columns = (
        "description_identity",
        "osm_type",
        "osm_id",
        "tag_key",
        "sentence_count",
    )
    for row in census._iter_parquet_rows(path, columns):
        polygon_id = _osm_polygon_id(row)
        if polygon_id not in source_polygon_ids:
            raise ValueError(
                "Description text row references a polygon missing from geometry data"
            )
        links = [polygon_id]
        for sentence_index in range(
            _sentence_count(row.get("sentence_count"), "sentence_count")
        ):
            yield {
                "description_identity": row.get("description_identity"),
                "tag_key": row.get("tag_key"),
                "sentence_index": sentence_index,
                "polygon_ids": links,
            }


def _website_text_units(
    path: Path,
    source_polygon_ids: set[str],
    sentence_count_fields: dict[str, str],
) -> Iterator[dict[str, Any]]:
    columns = ("polygon_id", *sentence_count_fields.values())
    for row in census._iter_parquet_rows(path, columns):
        polygon_id = _polygon_id(row, "website-polygons-v1")
        if polygon_id not in source_polygon_ids:
            raise ValueError("Website row references a missing geometry polygon")
        links = [polygon_id]
        for field, count_column in sentence_count_fields.items():
            value = row.get(count_column)
            # The pinned Website schema uses null for an absent optional field.
            count = 0 if value is None else _sentence_count(value, count_column)
            for sentence_index in range(count):
                yield {
                    "polygon_id": polygon_id,
                    "field": field,
                    "sentence_index": sentence_index,
                    "polygon_ids": links,
                }


def _wiki_document_links(
    path: Path, source_polygon_ids: set[str]
) -> tuple[dict[str, list[str]], int]:
    links: dict[str, set[str]] = defaultdict(set)
    row_count = 0
    for row in census._iter_parquet_rows(path, ("document_id", "polygon_id")):
        row_count += 1
        document_id = row.get("document_id")
        polygon_id = row.get("polygon_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("Wiki polygon-document link has an invalid document_id")
        if not isinstance(polygon_id, str) or not polygon_id:
            raise ValueError("Wiki polygon-document link has an invalid polygon_id")
        if polygon_id not in source_polygon_ids:
            raise ValueError("Wiki document link references a missing geometry polygon")
        links[document_id].add(polygon_id)
    if row_count != _parquet_row_count(path):
        raise ValueError("Wiki polygon-document link count differs from metadata")
    return {key: sorted(value) for key, value in links.items()}, row_count


def _wiki_text_units(
    path: Path, document_links: dict[str, list[str]]
) -> Iterator[dict[str, Any]]:
    for row in census._iter_parquet_rows(path, ("sentence_id", "document_id")):
        document_id = row.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("Wiki sentence row has an invalid document_id")
        yield {
            "sentence_id": row.get("sentence_id"),
            "polygon_ids": document_links.get(document_id, []),
        }


def _direct_text_units(
    source: dict[str, Any],
    stream_name: str,
    input_root: Path,
    region: str,
    source_polygon_ids: set[str],
    direct_input: dict[str, Any],
) -> tuple[Callable[[tuple[str, ...]], Iterator[dict[str, Any]]], dict[str, Any]]:
    schema = direct_input["schema"]
    if schema == "description-language-v1":
        path = _snapshot_file(
            input_root,
            direct_input["text_path_template"],
            region=region,
            stream=stream_name,
        )

        def factory(_columns: tuple[str, ...]) -> Iterator[dict[str, Any]]:
            return _description_text_units(path, source_polygon_ids)

        receipt = {
            "name": direct_input["text_path_template"].format(
                region=region, stream=stream_name
            ),
            "row_count": _parquet_row_count(path),
        }
        return factory, receipt
    if schema == "website-polygons-v1":
        path = _snapshot_file(
            input_root,
            direct_input["text_path_template"],
            region=region,
            stream=stream_name,
        )

        def factory(_columns: tuple[str, ...]) -> Iterator[dict[str, Any]]:
            return _website_text_units(
                path, source_polygon_ids, direct_input["sentence_count_fields"]
            )

        receipt = {
            "name": direct_input["text_path_template"].format(
                region=region, stream=stream_name
            ),
            "row_count": _parquet_row_count(path),
        }
        return factory, receipt
    links_path = _snapshot_file(
        input_root,
        direct_input["links_path_template"],
        region=region,
        stream=stream_name,
    )
    document_links, link_rows = _wiki_document_links(links_path, source_polygon_ids)
    stream = census._stream_config(source, stream_name)
    path = _snapshot_file(
        input_root,
        stream["text_path_template"],
        region=region,
        stream=stream_name,
    )

    def factory(_columns: tuple[str, ...]) -> Iterator[dict[str, Any]]:
        return _wiki_text_units(path, document_links)

    receipt = {
        "name": stream["text_path_template"].format(region=region, stream=stream_name),
        "row_count": _parquet_row_count(path),
        "polygon_document_links": {
            "name": direct_input["links_path_template"].format(
                region=region, stream=stream_name
            ),
            "row_count": link_rows,
        },
    }
    return factory, receipt


def _direct_labels(path: Path, key_fields: tuple[str, ...]) -> Iterator[dict[str, Any]]:
    columns = (*key_fields, "decision", "input_revision")
    for row in census._iter_parquet_rows(path, columns):
        decision = row.get("decision")
        status = "skipped" if decision == "skipped_unsplit" else decision
        yield {
            **{field: row.get(field) for field in key_fields},
            "status": status,
            "input_revision": row.get("input_revision"),
        }


def _run_direct_region_stream(
    *,
    source: dict[str, Any],
    stream_name: str,
    stream: dict[str, str],
    region: str,
    input_root: Path,
    label_root: Path,
    gpkg_path: Path,
    transformer: Any,
    lucas_sha256: str,
) -> dict[str, Any]:
    direct_input = source["direct_input"]
    polygon_path = _snapshot_file(
        input_root,
        direct_input["polygon_path_template"],
        region=region,
        stream=stream_name,
    )
    geometries, polygon_ids, geometry_exclusions, decisions, polygon_rows = (
        _load_direct_polygons(
            polygon_path,
            direct_input["schema"],
            transformer,
            source["geometry_encoding"],
        )
    )
    if geometries:
        (
            eligible,
            coverage,
            rejections,
            support_rows,
            support_accepted,
            decisions,
            _,
        ) = census._census_polygons(
            geometries, census._bounds(geometries), gpkg_path, decisions
        )
    else:
        eligible = {}
        coverage = Counter()
        rejections = Counter()
        support_rows = support_accepted = 0
    text_factory, text_receipt = _direct_text_units(
        source,
        stream_name,
        input_root,
        region,
        polygon_ids,
        direct_input,
    )
    label_path = _snapshot_file(
        label_root,
        stream["labels_path_template"],
        region=region,
        stream=stream_name,
    )
    key_fields = tuple(source["join_key_fields"])
    counts = census.count_eligible_source_sentences(
        eligible,
        text_factory,
        _direct_labels(label_path, key_fields),
        key_fields=key_fields,
        stream_name=stream_name,
        polygon_ids=polygon_ids,
        expected_input_revision=source["input_revision"],
    )
    if sum(counts["source_label_rows"].values()) != _parquet_row_count(label_path):
        raise ValueError("source label row count differs from Parquet metadata")
    label_receipt = {
        "name": stream["labels_path_template"].format(
            region=region, stream=stream_name
        ),
        "row_count": sum(counts["source_label_rows"].values()),
    }
    return {
        "region": region,
        "stream": stream_name,
        "source_polygon_records": len(polygon_ids),
        "eligible_reference_polygons_by_code": dict(
            sorted(Counter(eligible.values()).items())
        ),
        "coverage_outcomes": {
            name: coverage[name] for name in census._COVERAGE_OUTCOMES
        },
        "geometry_exclusions": {
            name: geometry_exclusions[name] for name in census._GEOMETRY_EXCLUSIONS
        },
        "eligible_source_relevance_counts": counts,
        "support_scan": {
            "rtree_bbox_candidate_rows": support_rows,
            "accepted_support_rows": support_accepted,
            "rejected_support_rows": support_rows - support_accepted,
            "rejection_reason_counts": dict(sorted(rejections.items())),
        },
        "input_receipt": {
            "source_polygon_records": {
                "name": direct_input["polygon_path_template"].format(
                    region=region, stream=stream_name
                ),
                "row_count": polygon_rows,
            },
            "text_units": text_receipt,
            "labels": label_receipt,
            "source_repository_revision": source["input_revision"],
            "label_repository_revision": source["label_revision"],
            "lucas_asset_sha256_verified_this_run": lucas_sha256,
        },
        "eligibility_decision_sha256": hashlib.sha256(
            json.dumps(sorted(decisions.items()), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def run_direct_source_pool_census(
    input_snapshot: Path,
    label_snapshot: Path,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
    source_name: str,
    *,
    regions: list[str] | None = None,
    stream_names: list[str] | None = None,
) -> dict[str, Any]:
    """Run from existing exact-revision HF cache snapshots; never downloads."""
    config = census._load_config(config_path)
    source = census._source_config(config, source_name)
    input_root = _validate_snapshot_root(
        input_snapshot, source["input_repository"], source["input_revision"]
    )
    label_root = _validate_snapshot_root(
        label_snapshot, source["label_repository"], source["label_revision"]
    )
    selected_regions = list(config["scope"]["regions"] if regions is None else regions)
    if not selected_regions or len(selected_regions) != len(set(selected_regions)):
        raise ValueError("requested regions must be non-empty and unique")
    unknown_regions = set(selected_regions) - set(config["scope"]["regions"])
    if unknown_regions:
        raise ValueError(f"regions outside pinned scope: {sorted(unknown_regions)}")
    selected_streams = list(
        stream_names or [stream["name"] for stream in source["streams"]]
    )
    if len(selected_streams) != len(set(selected_streams)):
        raise ValueError("requested streams must be unique")
    streams = [census._stream_config(source, name) for name in selected_streams]
    lucas_config = json.loads(lucas_source_config_path.read_text(encoding="utf-8"))
    asset_signature = census._asset_signature(gpkg_path)
    lucas_sha256 = census._verify_lucas_asset(gpkg_path, lucas_config)
    census._require_asset_signature(gpkg_path, asset_signature)
    transformer = census._projection()
    results_by_stream: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for stream_name, stream in zip(selected_streams, streams, strict=True):
        results = []
        for region in selected_regions:
            census._require_asset_signature(gpkg_path, asset_signature)
            results.append(
                _run_direct_region_stream(
                    source=source,
                    stream_name=stream_name,
                    stream=stream,
                    region=region,
                    input_root=input_root,
                    label_root=label_root,
                    gpkg_path=gpkg_path,
                    transformer=transformer,
                    lucas_sha256=lucas_sha256,
                )
            )
            census._require_asset_signature(gpkg_path, asset_signature)
        results_by_stream[stream_name] = results
        summaries[stream_name] = census._sum_region_results(results)
    all_results = [
        result
        for stream_results in results_by_stream.values()
        for result in stream_results
    ]
    return {
        "report_schema": "lucas-source-pool-census/v3",
        "input_mode": "local-pinned-hf-cache-snapshots",
        "source_name": source_name,
        "source_id": source["source_id"],
        "source_display_label": source.get("display_label", source_name),
        "scope": {
            "reference": config["scope"]["reference"],
            "regions": selected_regions,
            "configured_region_count": len(config["scope"]["regions"]),
            "selected_streams": selected_streams,
            "scope_complete": len(selected_regions) == len(config["scope"]["regions"])
            and set(selected_streams)
            == {stream["name"] for stream in source["streams"]},
        },
        "method": census.method_report(config),
        "source_pins": {
            key: source[key]
            for key in (
                "input_repository",
                "input_revision",
                "label_repository",
                "label_revision",
                "source_id",
                "geometry_encoding",
                "join_key_fields",
            )
        },
        "snapshot_roots": {
            "input": {
                "repository": source["input_repository"],
                "revision": source["input_revision"],
            },
            "labels": {
                "repository": source["label_repository"],
                "revision": source["label_revision"],
            },
            "network_access": "none; the adapter reads only existing local snapshots",
        },
        "lucas_asset": {
            "sha256": lucas_sha256,
            "size_bytes": lucas_config["size_bytes"],
            "size_verified_this_run": True,
            "sha256_verified_this_run": True,
            "asset_identity_stable_during_scan": True,
        },
        "runtime_versions": {
            "pyarrow": importlib.import_module("pyarrow").__version__,
            "pyproj": importlib.import_module("pyproj").__version__,
            "shapely": shapely.__version__,
        },
        "tool_sha256": census._file_sha256(Path(__file__)),
        "coverage_api_sha256": {
            name: census._file_sha256(
                Path(__file__).resolve().parents[1]
                / "src/osm_polygon_lucas"
                / f"{name}.py"
            )
            for name in (
                "lucas_gpkg",
                "lucas_taxonomy",
                "polygon_coverage",
                "support_eligibility",
            )
        },
        "sampling": "not run; eligibility counts are reported before H3 sampling",
        "stream_summaries": summaries,
        "stream_results": results_by_stream,
        "aggregate_across_selected_streams": census._sum_region_results(all_results),
    }
