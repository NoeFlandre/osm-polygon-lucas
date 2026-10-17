"""Read-only, pinned LUCAS eligibility counts for the three source pools."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import shapely
from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from tools.lucas_description_census import (
    _asset_signature,
    _bounds,
    _census_polygons,
    _file_sha256,
    _require_asset_signature,
    _status_counts_match_manifest,
    _verified_table_file,
    _verify_lucas_asset,
)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/lucas-source-pools.json"
_LUCAS_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config/lucas-2022-source.json"
)
_GEOMETRY_EXCLUSIONS = (
    "invalid_wkb",
    "invalid_geojson",
    "non_polygon_geometry",
    "outside_crs84_bounds",
    "invalid_geometry",
    "zero_area",
    "invalid_after_projection",
    "zero_area_after_projection",
)
_COVERAGE_OUTCOMES = (
    "eligible",
    "below_threshold",
    "ambiguous",
    "unknown_unmapped_support",
    "no_overlap",
    "boundary_only",
)
_STATUS_NAMES = ("yes", "no", "skipped", "failed")
_BATCH_SIZE = 20_000
_SEEN_ELIGIBLE_KEY = object()
_GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_DIRECT_SCHEMAS = {
    "description-language-v1",
    "website-polygons-v1",
    "wiki-document-sentences-v1",
}


def method_report(config: dict[str, Any]) -> dict[str, Any]:
    """Return the exact spatial rule and exclusions attached to every report."""
    return {
        "reference": config["scope"]["reference"],
        **config["eligibility"],
        "label_join_stage": "join source labels after spatial eligibility",
        "source_label_semantics": (
            "yes/no are existing source relevance labels, separate from the "
            "LUCAS main_category reference code"
        ),
        "sampling": "not run by this census",
    }


def _load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported source-pool config schema")
    regions = config["scope"]["regions"]
    if len(regions) != len(set(regions)) or not regions:
        raise ValueError("source-pool region scope must be non-empty and unique")
    if set(config["sources"]) != {"Description", "Website", "Wikidata"}:
        raise ValueError("config must define Description, Website, and Wikidata")
    if config["eligibility"]["minimum_class_fraction"] != 0.8:
        raise ValueError("the pinned LUCAS threshold must remain 0.80")
    if config["eligibility"]["uncovered_polygons_are_negative"] is not False:
        raise ValueError("uncovered polygons cannot be labeled negative")
    for source_name, source in config["sources"].items():
        for pin_name in ("input_revision", "label_revision"):
            revision = source.get(pin_name)
            if not isinstance(revision, str) or _GIT_SHA1.fullmatch(revision) is None:
                raise ValueError(
                    f"{source_name} {pin_name} must be a 40-character "
                    "lowercase Git SHA-1"
                )
        if source["geometry_encoding"] not in {"wkb", "geojson"}:
            raise ValueError("unsupported source geometry encoding")
        if not source["join_key_fields"] or not source["streams"]:
            raise ValueError("each source needs a join key and at least one stream")
        direct_input = source.get("direct_input")
        if (
            not isinstance(direct_input, dict)
            or direct_input.get("schema") not in _DIRECT_SCHEMAS
        ):
            raise ValueError(f"{source_name} has an unsupported direct-input schema")
        _validate_direct_input(source_name, source)
    return config


def _validate_direct_input(source_name: str, source: dict[str, Any]) -> None:
    direct_input = source["direct_input"]
    schema = direct_input["schema"]
    required = {
        "description-language-v1": (
            "polygon_path_template",
            "text_path_template",
        ),
        "website-polygons-v1": (
            "polygon_path_template",
            "text_path_template",
            "sentence_count_fields",
        ),
        "wiki-document-sentences-v1": (
            "polygon_path_template",
            "links_path_template",
        ),
    }[schema]
    for name in required:
        if name not in direct_input:
            raise ValueError(f"{source_name} direct_input is missing {name}")
    templates = [
        value for name, value in direct_input.items() if name.endswith("path_template")
    ]
    if schema == "wiki-document-sentences-v1":
        for stream in source["streams"]:
            if not isinstance(stream.get("text_path_template"), str):
                raise ValueError(f"{source_name} Wiki stream needs a text path")
            templates.append(stream["text_path_template"])
    for template in templates:
        if not isinstance(template, str) or not template:
            raise ValueError(f"{source_name} direct-source path template is invalid")
        try:
            rendered = template.format(region="albania", stream="wikipedia")
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"{source_name} direct-source path template is invalid"
            ) from exc
        path = Path(rendered)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"{source_name} direct-source paths must stay in the snapshot"
            )
    if schema == "website-polygons-v1":
        fields = direct_input["sentence_count_fields"]
        if (
            not isinstance(fields, dict)
            or set(fields)
            != {
                "website",
                "contact_website",
            }
            or any(not isinstance(value, str) or not value for value in fields.values())
        ):
            raise ValueError(f"{source_name} Website sentence-count fields are invalid")


def _source_config(config: dict[str, Any], source_name: str) -> dict[str, Any]:
    try:
        return config["sources"][source_name]
    except KeyError as exc:
        raise ValueError(f"unknown source pool: {source_name}") from exc


def _stream_config(source: dict[str, Any], stream_name: str) -> dict[str, str]:
    matches = [stream for stream in source["streams"] if stream["name"] == stream_name]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate source stream: {stream_name}")
    return matches[0]


def _plan_index(plan: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for partition in plan["partitions"]:
        key = (partition["source_id"], partition["labels_path"])
        if key in result:
            raise ValueError(f"duplicate Atlas partition plan entry: {key!r}")
        result[key] = partition
    return result


def _manifest_path(
    atlas_root: Path,
    plan_index: dict[tuple[str, str], dict[str, Any]],
    source: dict[str, Any],
    stream: dict[str, str],
    region: str,
) -> tuple[Path, str] | None:
    labels_path = stream["labels_path_template"].format(region=region)
    relative = Path(labels_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("labels path template must remain within the Atlas build")
    partition = plan_index.get((source["source_id"], labels_path))
    if partition is None:
        return None
    stem = labels_path.replace("/", "-").removesuffix(".parquet")
    manifest_path = atlas_root / f"{source['source_id']}__{stem}.manifest.json"
    return manifest_path, labels_path


def _validate_manifest(
    manifest: dict[str, Any], source: dict[str, Any], region: str
) -> None:
    if manifest.get("source_id") != source["source_id"]:
        raise ValueError(f"{region}: manifest source_id differs from the pin")
    if manifest.get("repo_id") != source["label_repository"]:
        raise ValueError(f"{region}: label repository differs from the pin")
    if manifest.get("revision") != source["label_revision"]:
        raise ValueError(f"{region}: label revision differs from the pin")


def _validate_plan_source(plan: dict[str, Any], source: dict[str, Any]) -> None:
    matches = [
        entry for entry in plan["sources"] if entry["source_id"] == source["source_id"]
    ]
    if len(matches) != 1:
        raise ValueError("Atlas plan does not have exactly one matching source")
    entry = matches[0]
    if (
        entry.get("repo_id") != source["label_repository"]
        or entry.get("revision") != source["label_revision"]
    ):
        raise ValueError("Atlas plan source pin differs from the cohort config")


def _iter_parquet_rows(
    path: Path, columns: tuple[str, ...]
) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "install PyArrow to read pinned source or Atlas parquet files"
        ) from exc
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(
        batch_size=_BATCH_SIZE, columns=list(columns), use_threads=False
    ):
        yield from batch.to_pylist()


def _key(row: dict[str, Any], key_fields: tuple[str, ...]) -> tuple[Any, ...]:
    values: list[Any] = []
    for field in key_fields:
        value = row.get(field)
        if field == "sentence_index":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("sentence_index must be a non-negative integer")
        elif not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be non-empty text")
        values.append(value)
    return tuple(values)


def sentence_columns(source: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Select join and status fields only; never request source sentence text."""
    key_fields = tuple(source["join_key_fields"])
    return (*key_fields, "polygon_ids"), (*key_fields, "status", "input_revision")


def _text_unit_row_factory(
    rows: Iterable[dict[str, Any]]
    | Callable[[tuple[str, ...]], Iterable[dict[str, Any]]],
) -> Callable[[tuple[str, ...]], Iterable[dict[str, Any]]]:
    if isinstance(rows, Callable):
        return rows
    if isinstance(rows, Iterator):
        raise TypeError("text_units must be re-iterable or supplied as a row factory")
    return lambda columns: iter(rows)


def count_eligible_source_sentences(
    eligible_polygon_reference_codes: dict[str, str],
    text_units: Iterable[dict[str, Any]]
    | Callable[[tuple[str, ...]], Iterable[dict[str, Any]]],
    labels: Iterable[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
    stream_name: str,
    polygon_ids: set[str],
    expected_input_revision: str,
) -> dict[str, Any]:
    """Join only source label keys linked to eligible polygons, in bounded memory."""
    if not key_fields:
        raise ValueError("source sentence join key cannot be empty")
    read_text_units = _text_unit_row_factory(text_units)
    coverage_by_key: dict[tuple[Any, ...], set[Any]] = {}
    text_unit_rows = 0
    dangling_links = 0
    for row in read_text_units((*key_fields, "polygon_ids")):
        text_unit_rows += 1
        linked = row.get("polygon_ids")
        if not isinstance(linked, list):
            raise ValueError("source text unit polygon_ids must be a list")
        if any(
            not isinstance(polygon_id, str) or not polygon_id for polygon_id in linked
        ):
            raise ValueError(
                "source text unit polygon_ids must contain non-empty strings"
            )
        dangling_links += sum(polygon_id not in polygon_ids for polygon_id in linked)
        key = (stream_name, *_key(row, key_fields))
        reference_codes = {
            eligible_polygon_reference_codes[polygon_id]
            for polygon_id in linked
            if polygon_id in eligible_polygon_reference_codes
        }
        if reference_codes:
            if key in coverage_by_key:
                raise ValueError("duplicate source text-unit key")
            coverage_by_key[key] = reference_codes
    if dangling_links:
        raise ValueError(
            f"source text units contain {dangling_links} unknown polygon links"
        )

    duplicate_validation_rows = 0
    if coverage_by_key:
        for row in read_text_units(key_fields):
            duplicate_validation_rows += 1
            key = (stream_name, *_key(row, key_fields))
            reference_codes = coverage_by_key.get(key)
            if reference_codes is None:
                continue
            if _SEEN_ELIGIBLE_KEY in reference_codes:
                raise ValueError("duplicate source text-unit key")
            reference_codes.add(_SEEN_ELIGIBLE_KEY)
        if duplicate_validation_rows != text_unit_rows:
            raise ValueError(
                "source text units changed during duplicate-key validation"
            )
        for reference_codes in coverage_by_key.values():
            if _SEEN_ELIGIBLE_KEY not in reference_codes:
                raise ValueError("eligible source text-unit key was not re-read")
            reference_codes.remove(_SEEN_ELIGIBLE_KEY)

    source_statuses: Counter[str] = Counter()
    joined_statuses: Counter[str] = Counter()
    by_reference: dict[str, dict[str, Any]] = {}
    joined_keys: set[tuple[Any, ...]] = set()
    decision_rows: list[tuple[tuple[Any, ...], str, str]] = []
    input_revisions: set[str] = set()
    for row in labels:
        status = row.get("status")
        revision = row.get("input_revision")
        if not isinstance(status, str) or not status:
            raise ValueError("source label status must be non-empty text")
        if not isinstance(revision, str):
            raise ValueError("source label input_revision must be text")
        source_statuses[status] += 1
        input_revisions.add(revision)
        if not coverage_by_key:
            continue
        key = (stream_name, *_key(row, key_fields))
        if key not in coverage_by_key:
            continue
        if key in joined_keys:
            raise ValueError("duplicate eligible source label key")
        joined_keys.add(key)
        joined_statuses[status] += 1
        for reference_code in sorted(coverage_by_key[key]):
            bucket = by_reference.setdefault(
                reference_code,
                {
                    "source_relevance_yes": set(),
                    "source_relevance_no": set(),
                    "non_binary_statuses": Counter(),
                    "linked_text_unit_rows": 0,
                },
            )
            bucket["linked_text_unit_rows"] += 1
            if status in {"yes", "no"}:
                bucket[f"source_relevance_{status}"].add(key)
                decision_rows.append((key, reference_code, status))
            else:
                bucket["non_binary_statuses"][status] += 1
                decision_rows.append((key, reference_code, f"excluded:{status}"))

    if source_statuses and input_revisions != {expected_input_revision}:
        raise ValueError("source labels do not use the pinned input revision")
    if joined_keys != set(coverage_by_key):
        raise ValueError("eligible source text units are missing pinned label rows")

    union_yes = set().union(
        *(bucket["source_relevance_yes"] for bucket in by_reference.values())
    )
    union_no = set().union(
        *(bucket["source_relevance_no"] for bucket in by_reference.values())
    )
    result_by_reference = {
        reference_code: {
            "linked_text_unit_rows": bucket["linked_text_unit_rows"],
            "source_relevance_yes": len(bucket["source_relevance_yes"]),
            "source_relevance_no": len(bucket["source_relevance_no"]),
            "non_binary_statuses": dict(sorted(bucket["non_binary_statuses"].items())),
        }
        for reference_code, bucket in sorted(by_reference.items())
    }
    digest_payload = json.dumps(
        sorted(decision_rows), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "text_unit_rows_scanned": text_unit_rows,
        "duplicate_validation_rows": duplicate_validation_rows,
        "by_lucas_reference_code": result_by_reference,
        "unique_eligible_sentence_instances_by_source_relevance": {
            "yes": len(union_yes),
            "no": len(union_no),
        },
        "text_units_linked_to_multiple_reference_codes": sum(
            len(codes) > 1 for codes in coverage_by_key.values()
        ),
        "source_label_rows": dict(sorted(source_statuses.items())),
        "joined_to_eligible_polygons": dict(sorted(joined_statuses.items())),
        "not_joined_to_eligible_polygons": {
            status: count - joined_statuses[status]
            for status, count in sorted(source_statuses.items())
        },
        "eligible_sentence_decision_sha256": hashlib.sha256(digest_payload).hexdigest(),
    }


def _decode_geometry(value: Any, encoding: str) -> BaseGeometry:
    if encoding == "wkb":
        return shapely.from_wkb(value)
    if encoding == "geojson":
        return shapely.from_geojson(value)
    raise ValueError(f"unsupported geometry encoding: {encoding}")


def _load_source_polygons(
    atlas_root: Path,
    manifest: dict[str, Any],
    transformer: Any,
    encoding: str,
) -> tuple[
    list[tuple[str, BaseGeometry]],
    set[str],
    Counter[str],
    dict[str, str],
    dict[str, Any],
]:
    path, entry = _verified_table_file(atlas_root, manifest, "polygon_records")
    geometries: list[tuple[str, BaseGeometry]] = []
    polygon_ids: set[str] = set()
    exclusions: Counter[str] = Counter()
    decisions: dict[str, str] = {}
    row_count = 0
    geometry_error = "invalid_wkb" if encoding == "wkb" else "invalid_geojson"
    for row in _iter_parquet_rows(path, ("polygon_id", "geometry")):
        row_count += 1
        polygon_id = row.get("polygon_id")
        if not isinstance(polygon_id, str) or not polygon_id:
            raise ValueError("polygon_records contains a missing polygon_id")
        if polygon_id in polygon_ids:
            raise ValueError(f"duplicate polygon_id in source partition {polygon_id!r}")
        polygon_ids.add(polygon_id)
        try:
            geometry = _decode_geometry(row.get("geometry"), encoding)
        except (ShapelyError, TypeError, ValueError):
            exclusions[geometry_error] += 1
            decisions[polygon_id] = geometry_error
            continue
        if not isinstance(geometry, Polygon | MultiPolygon):
            exclusions["non_polygon_geometry"] += 1
            decisions[polygon_id] = "non_polygon_geometry"
            continue
        if geometry.is_empty:
            exclusions["zero_area"] += 1
            decisions[polygon_id] = "zero_area"
            continue
        minx, miny, maxx, maxy = geometry.bounds
        if not all(math.isfinite(value) for value in geometry.bounds) or (
            minx < -180 or miny < -90 or maxx > 180 or maxy > 90
        ):
            exclusions["outside_crs84_bounds"] += 1
            decisions[polygon_id] = "outside_crs84_bounds"
            continue
        if not geometry.is_valid:
            exclusions["invalid_geometry"] += 1
            decisions[polygon_id] = "invalid_geometry"
            continue
        if geometry.area <= 0:
            exclusions["zero_area"] += 1
            decisions[polygon_id] = "zero_area"
            continue
        projected = shapely.transform(
            geometry, transformer.transform, interleaved=False
        )
        if not isinstance(projected, Polygon | MultiPolygon) or not projected.is_valid:
            exclusions["invalid_after_projection"] += 1
            decisions[polygon_id] = "invalid_after_projection"
            continue
        if projected.area <= 0:
            exclusions["zero_area_after_projection"] += 1
            decisions[polygon_id] = "zero_area_after_projection"
            continue
        geometries.append((polygon_id, projected))
    if row_count != entry["row_count"]:
        raise ValueError("polygon_records row count differs from its pinned manifest")
    return geometries, polygon_ids, exclusions, decisions, entry


def _projection() -> Any:
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError(
            "install pyproj to project source polygons into EPSG:3035"
        ) from exc
    return Transformer.from_crs("OGC:CRS84", "EPSG:3035", always_xy=True)


def _read_eligible_counts(
    atlas_root: Path,
    manifest: dict[str, Any],
    eligible: dict[str, str],
    polygon_ids: set[str],
    source: dict[str, Any],
    stream_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    text_path, text_entry = _verified_table_file(atlas_root, manifest, "text_units")
    labels_path, labels_entry = _verified_table_file(atlas_root, manifest, "labels")
    text_columns, label_columns = sentence_columns(source)
    key_fields = tuple(source["join_key_fields"])
    counts = count_eligible_source_sentences(
        eligible,
        lambda columns: _iter_parquet_rows(text_path, columns),
        _iter_parquet_rows(labels_path, label_columns),
        key_fields=key_fields,
        stream_name=stream_name,
        polygon_ids=polygon_ids,
        expected_input_revision=source["input_revision"],
    )
    if counts["text_unit_rows_scanned"] != text_entry["row_count"]:
        raise ValueError("text_units row count differs from its pinned manifest")
    if sum(counts["source_label_rows"].values()) != labels_entry["row_count"]:
        raise ValueError("labels row count differs from its pinned manifest")
    if not _status_counts_match_manifest(
        counts["source_label_rows"], manifest.get("status_counts", {})
    ):
        raise ValueError("label status totals differ from the pinned manifest")
    inputs = {
        "text_units": {"name": text_path.name, "sha256": text_entry["sha256"]},
        "labels": {"name": labels_path.name, "sha256": labels_entry["sha256"]},
    }
    return counts, inputs


def _run_region_stream(
    atlas_root: Path,
    gpkg_path: Path,
    region: str,
    stream_name: str,
    stream: dict[str, str],
    source: dict[str, Any],
    plan: dict[str, Any],
    plan_index: dict[tuple[str, str], dict[str, Any]],
    lucas_sha256: str,
    transformer: Any,
) -> dict[str, Any]:
    planned = _manifest_path(atlas_root, plan_index, source, stream, region)
    if planned is None:
        raise ValueError(
            f"{region}/{stream_name}: labels path is not in the pinned Atlas plan"
        )
    manifest_path, labels_path = planned
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"{region}/{stream_name}: pinned Atlas manifest is not complete"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_manifest(manifest, source, region)
    geometries, polygon_ids, geometry_exclusions, decisions, polygon_entry = (
        _load_source_polygons(
            atlas_root, manifest, transformer, source["geometry_encoding"]
        )
    )
    if geometries:
        bounds = _bounds(geometries)
        (
            eligible,
            coverage,
            rejections,
            support_rows,
            support_accepted,
            decisions,
            _,
        ) = _census_polygons(geometries, bounds, gpkg_path, decisions)
    else:
        eligible = {}
        coverage = Counter()
        rejections = Counter()
        support_rows = 0
        support_accepted = 0
    counts, sentence_inputs = _read_eligible_counts(
        atlas_root, manifest, eligible, polygon_ids, source, stream_name
    )
    decision_digest = hashlib.sha256(
        json.dumps(sorted(decisions.items()), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "region": region,
        "stream": stream_name,
        "source_polygon_records": len(polygon_ids),
        "eligible_reference_polygons_by_code": dict(
            sorted(Counter(eligible.values()).items())
        ),
        "coverage_outcomes": {name: coverage[name] for name in _COVERAGE_OUTCOMES},
        "geometry_exclusions": {
            name: geometry_exclusions[name] for name in _GEOMETRY_EXCLUSIONS
        },
        "eligible_source_relevance_counts": counts,
        "support_scan": {
            "rtree_bbox_candidate_rows": support_rows,
            "accepted_support_rows": support_accepted,
            "rejected_support_rows": support_rows - support_accepted,
            "rejection_reason_counts": dict(sorted(rejections.items())),
        },
        "input_receipt": {
            "atlas_manifest": {
                "name": manifest_path.name,
                "sha256": _file_sha256(manifest_path),
                "labels_path": labels_path,
                "label_repository_revision": manifest["revision"],
                "source_input_revision": source["input_revision"],
            },
            "polygon_records": {
                "name": polygon_entry["name"],
                "sha256": polygon_entry["sha256"],
            },
            **sentence_inputs,
            "lucas_asset_sha256_verified_this_run": lucas_sha256,
        },
        "eligibility_decision_sha256": decision_digest,
    }


def _sum_region_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = Counter()
    geometry = Counter()
    labels = Counter()
    joined = Counter()
    eligible = Counter()
    polygons = 0
    for result in results:
        coverage.update(result["coverage_outcomes"])
        geometry.update(result["geometry_exclusions"])
        count = result["eligible_source_relevance_counts"]
        labels.update(count["source_label_rows"])
        joined.update(count["joined_to_eligible_polygons"])
        eligible.update(count["unique_eligible_sentence_instances_by_source_relevance"])
        polygons += result["source_polygon_records"]
    return {
        "source_polygon_records": polygons,
        "coverage_outcomes": {name: coverage[name] for name in _COVERAGE_OUTCOMES},
        "geometry_exclusions": {name: geometry[name] for name in _GEOMETRY_EXCLUSIONS},
        "source_label_rows": {name: labels[name] for name in sorted(labels)},
        "joined_label_rows": {name: joined[name] for name in sorted(joined)},
        "eligible_source_relevance_instances": {
            "yes": eligible["yes"],
            "no": eligible["no"],
        },
        "shortfall_from_target": {
            "yes": max(0, 500 - eligible["yes"]),
            "no": max(0, 500 - eligible["no"]),
        },
    }


def run_source_pool_census(
    atlas_root: Path,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
    source_name: str,
    *,
    regions: list[str] | None = None,
    stream_names: list[str] | None = None,
) -> dict[str, Any]:
    config = _load_config(config_path)
    source = _source_config(config, source_name)
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
    streams = [_stream_config(source, name) for name in selected_streams]
    lucas_config = json.loads(lucas_source_config_path.read_text(encoding="utf-8"))
    asset_signature = _asset_signature(gpkg_path)
    lucas_sha256 = _verify_lucas_asset(gpkg_path, lucas_config)
    _require_asset_signature(gpkg_path, asset_signature)

    plan = json.loads((atlas_root / "assembly-plan.json").read_text(encoding="utf-8"))
    _validate_plan_source(plan, source)
    plan_index = _plan_index(plan)
    transformer = _projection()
    results_by_stream: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for stream_name, stream in zip(selected_streams, streams, strict=True):
        results = []
        for region in selected_regions:
            _require_asset_signature(gpkg_path, asset_signature)
            results.append(
                _run_region_stream(
                    atlas_root,
                    gpkg_path,
                    region,
                    stream_name,
                    stream,
                    source,
                    plan,
                    plan_index,
                    lucas_sha256,
                    transformer,
                )
            )
            _require_asset_signature(gpkg_path, asset_signature)
        results_by_stream[stream_name] = results
        summaries[stream_name] = _sum_region_results(results)
    all_results = [
        result
        for stream_results in results_by_stream.values()
        for result in stream_results
    ]
    return {
        "report_schema": "lucas-source-pool-census/v2",
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
        "method": method_report(config),
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
        "atlas_plan": {
            "plan_sha256": plan["plan_sha256"],
            "implementation_revision": plan["implementation_revision"],
            "sha256": _file_sha256(atlas_root / "assembly-plan.json"),
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
        "tool_sha256": _file_sha256(Path(__file__)),
        "coverage_api_sha256": {
            name: _file_sha256(
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
        "aggregate_across_selected_streams": _sum_region_results(all_results),
    }


def run_availability_report(
    atlas_root: Path,
    config_path: Path,
    source_names: list[str] | None = None,
) -> dict[str, Any]:
    """Read Atlas manifests and report source rows before LUCAS filtering."""
    config = _load_config(config_path)
    selected_sources = list(config["sources"] if source_names is None else source_names)
    plan = json.loads((atlas_root / "assembly-plan.json").read_text(encoding="utf-8"))
    plan_index = _plan_index(plan)
    result_sources: dict[str, Any] = {}
    for source_name in selected_sources:
        source = _source_config(config, source_name)
        _validate_plan_source(plan, source)
        stream_results: dict[str, Any] = {}
        for stream in source["streams"]:
            statuses: Counter[str] = Counter()
            rows: Counter[str] = Counter()
            sizes: Counter[str] = Counter()
            completed: list[str] = []
            missing: list[str] = []
            for region in config["scope"]["regions"]:
                planned = _manifest_path(atlas_root, plan_index, source, stream, region)
                if planned is None:
                    missing.append(region)
                    continue
                manifest_path, _ = planned
                if not manifest_path.is_file():
                    missing.append(region)
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                _validate_manifest(manifest, source, region)
                completed.append(region)
                statuses.update(manifest.get("status_counts", {}))
                for name, entry in manifest.get("outputs", {}).items():
                    rows[name] += entry.get("row_count", 0)
                    sizes[name] += entry.get("size_bytes", 0)
            stream_results[stream["name"]] = {
                "complete_regions": len(completed),
                "expected_regions": len(config["scope"]["regions"]),
                "missing_regions": missing,
                "pre_lucas_source_label_rows": dict(sorted(statuses.items())),
                "output_rows": dict(sorted(rows.items())),
                "output_bytes": dict(sorted(sizes.items())),
                "raw_yes_no_pool_at_least_500_each": statuses["yes"] >= 500
                and statuses["no"] >= 500,
            }
        result_sources[source_name] = {
            "source_id": source["source_id"],
            "source_pins": {
                "label_repository": source["label_repository"],
                "label_revision": source["label_revision"],
                "input_revision": source["input_revision"],
            },
            "streams": stream_results,
        }
    return {
        "report_schema": "lucas-source-pool-availability/v1",
        "scope": {
            "reference": config["scope"]["reference"],
            "regions": config["scope"]["regions"],
        },
        "method": method_report(config),
        "spatial_eligibility": "not run; counts are pre-LUCAS availability only",
        "atlas_plan": {
            "plan_sha256": plan["plan_sha256"],
            "sha256": _file_sha256(atlas_root / "assembly-plan.json"),
        },
        "sources": result_sources,
    }


def _write_new_json(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-root", type=Path)
    parser.add_argument("--source-snapshot", type=Path)
    parser.add_argument("--label-snapshot", type=Path)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--lucas-gpkg", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path, default=_CONFIG_PATH)
    parser.add_argument("--lucas-source-config", type=Path, default=_LUCAS_CONFIG_PATH)
    parser.add_argument("--source", choices=("Description", "Website", "Wikidata"))
    parser.add_argument("--region", action="append", dest="regions")
    parser.add_argument("--stream", action="append", dest="streams")
    parser.add_argument("--availability-only", action="store_true")
    args = parser.parse_args()
    if args.availability_only:
        if (
            args.source
            or args.region
            or args.stream
            or args.lucas_gpkg
            or args.source_snapshot
            or args.label_snapshot
            or args.checkpoint_dir
        ):
            parser.error(
                "availability mode reads all pinned manifests; "
                "omit source/region/stream/gpkg/checkpoint-dir"
            )
        if args.atlas_root is None:
            parser.error("availability mode requires --atlas-root")
        result = run_availability_report(args.atlas_root, args.config)
    else:
        if not args.source or args.lucas_gpkg is None or args.output is None:
            parser.error(
                "census mode requires --source, --lucas-gpkg, and a new --output path"
            )
        if args.source_snapshot or args.label_snapshot:
            if args.atlas_root is not None:
                parser.error("choose either Atlas or direct source snapshots")
            if args.source_snapshot is None or args.label_snapshot is None:
                parser.error(
                    "direct mode requires both --source-snapshot and --label-snapshot"
                )
            from tools.lucas_direct_source_snapshot import (
                run_direct_source_pool_census,
            )

            result = run_direct_source_pool_census(
                args.source_snapshot,
                args.label_snapshot,
                args.lucas_gpkg,
                args.config,
                args.lucas_source_config,
                args.source,
                regions=args.regions,
                stream_names=args.streams,
                checkpoint_dir=args.checkpoint_dir,
            )
        else:
            if args.checkpoint_dir is not None:
                parser.error("--checkpoint-dir requires direct source snapshots")
            if args.atlas_root is None:
                parser.error(
                    "census mode requires --atlas-root or both pinned source snapshots"
                )
            result = run_source_pool_census(
                args.atlas_root,
                args.lucas_gpkg,
                args.config,
                args.lucas_source_config,
                args.source,
                regions=args.regions,
                stream_names=args.streams,
            )
    if args.output is None:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        try:
            _write_new_json(args.output, result)
        except FileExistsError:
            parser.error(f"refusing to overwrite existing report: {args.output}")
        print(
            json.dumps(
                {"report": str(args.output), "report_schema": result["report_schema"]},
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
