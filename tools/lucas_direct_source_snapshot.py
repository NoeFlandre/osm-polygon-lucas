"""Read-only LUCAS census adapter for locally cached pinned source snapshots."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import uuid
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
    """Check cache path components, not source-byte provenance; never download."""
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


def _validate_checkpoint_directory(
    checkpoint_dir: Path | None, *snapshot_roots: Path
) -> Path | None:
    if checkpoint_dir is None:
        return None
    checkpoint_root = checkpoint_dir.expanduser().resolve()
    for snapshot_root in snapshot_roots:
        snapshot = snapshot_root.resolve()
        if (
            checkpoint_root == snapshot
            or checkpoint_root in snapshot.parents
            or snapshot in checkpoint_root.parents
        ):
            raise ValueError("checkpoint directory must be separate from HF snapshots")
    return checkpoint_root


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


def _checkpoint_digest(identity: dict[str, Any]) -> str:
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checkpoint_path(
    root: Path | None,
    stage: str,
    identity: dict[str, Any],
) -> tuple[Path | None, str]:
    digest = _checkpoint_digest(identity)
    if root is None:
        return None, digest
    name = "-".join(
        str(identity[key])
        for key in ("source_id", "region", "stream")
        if identity.get(key) is not None
    )
    name = name or "asset"
    return root / stage / f"{name}-{digest[:16]}.json", digest


def _input_file_fingerprint(
    path: Path, *, include_content_hash: bool
) -> tuple[dict[str, Any], tuple[int, int, int, int, int]]:
    """Fingerprint a file for checkpoints and return its stability signature."""
    signature = census._asset_signature(path)
    fingerprint: dict[str, Any] = {"size_bytes": signature[2]}
    if include_content_hash:
        fingerprint["sha256"] = census._file_sha256(path)
    _require_input_file_signature(path, signature)
    return fingerprint, signature


def _require_input_file_signature(
    path: Path, expected: tuple[int, int, int, int, int]
) -> None:
    if not path.is_file() or census._asset_signature(path) != expected:
        raise ValueError(f"pinned snapshot file changed during the census: {path}")


def _complete_source_scope(
    config: dict[str, Any],
    source: dict[str, Any],
    selected_regions: list[str],
    selected_streams: list[str],
) -> bool:
    return set(selected_regions) == set(config["scope"]["regions"]) and set(
        selected_streams
    ) == {stream["name"] for stream in source["streams"]}


def _read_checkpoint(
    path: Path | None,
    schema: str,
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(checkpoint, dict):
        raise ValueError(f"invalid checkpoint at {path}")
    if checkpoint.get("checkpoint_schema") != schema:
        raise ValueError(f"unsupported checkpoint schema at {path}")
    if checkpoint.get("identity") != identity:
        raise ValueError(f"checkpoint identity mismatch at {path}")
    result = checkpoint.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"checkpoint result is invalid at {path}")
    if checkpoint.get("result_sha256") != _payload_sha256(result):
        raise ValueError(f"checkpoint payload digest mismatch at {path}")
    return result


def _write_checkpoint(
    path: Path | None,
    schema: str,
    identity: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "checkpoint_schema": schema,
        "identity": identity,
        "result_sha256": _payload_sha256(result),
        "result": result,
    }
    contents = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True) + "\n"
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            existing = _read_checkpoint(path, schema, identity)
            if existing != result:
                raise ValueError(
                    f"conflicting checkpoint already exists at {path}"
                ) from None
    finally:
        temp_path.unlink(missing_ok=True)


def _verify_or_reuse_lucas_asset(
    path: Path,
    config: dict[str, Any],
    lucas_config_path: Path,
    checkpoint_dir: Path | None,
    checkpoint_code_identity: dict[str, Any],
) -> tuple[tuple[int, int, int, int, int], str, bool]:
    signature = census._asset_signature(path)
    identity = {
        "stage": "lucas-asset",
        "asset_path": str(path.resolve()),
        "asset_signature": list(signature),
        "expected_sha256": config["sha256"],
        "expected_size_bytes": config["size_bytes"],
        "lucas_config_sha256": census._file_sha256(lucas_config_path),
        "code_identity": checkpoint_code_identity,
    }
    checkpoint_path, _ = _checkpoint_path(checkpoint_dir, "assets", identity)
    cached = _read_checkpoint(checkpoint_path, "lucas-asset-verification/v1", identity)
    if cached is not None:
        if cached.get("sha256") != config["sha256"]:
            raise ValueError("cached LUCAS digest differs from the pinned config")
        census._require_asset_signature(path, signature)
        return signature, cached["sha256"], True
    digest = census._verify_lucas_asset(path, config)
    _write_checkpoint(
        checkpoint_path,
        "lucas-asset-verification/v1",
        identity,
        {"sha256": digest, "size_bytes": signature[2]},
    )
    census._require_asset_signature(path, signature)
    return signature, digest, False


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
    if geometry.is_empty:
        return None, "zero_area"
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


def _rendered_snapshot_path(
    root: Path, template: str, *, region: str, stream: str
) -> tuple[Path, str]:
    relative = PurePosixPath(template.format(region=region, stream=stream))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("direct-source path template must stay inside its snapshot")
    return root.joinpath(*relative.parts), relative.as_posix()


def _direct_geometry_checkpoint(
    *,
    source: dict[str, Any],
    region: str,
    input_root: Path,
    gpkg_path: Path,
    transformer: Any,
    lucas_sha256: str,
    checkpoint_dir: Path | None,
    checkpoint_base: dict[str, Any],
) -> tuple[Path | None, str, dict[str, Any], dict[str, Any], bool]:
    direct_input = source["direct_input"]
    polygon_path, polygon_relative = _rendered_snapshot_path(
        input_root,
        direct_input["polygon_path_template"],
        region=region,
        stream="",
    )
    if not polygon_path.is_file():
        raise FileNotFoundError(
            f"pinned source snapshot file is missing: {polygon_relative}"
        )
    polygon_fingerprint, polygon_signature = _input_file_fingerprint(
        polygon_path, include_content_hash=checkpoint_dir is not None
    )
    identity = {
        **checkpoint_base,
        "stage": "geometry",
        "region": region,
        "stream": None,
        "polygon_path": polygon_relative,
        "polygon_size_bytes": polygon_fingerprint["size_bytes"],
        "geometry_encoding": source["geometry_encoding"],
    }
    if "sha256" in polygon_fingerprint:
        identity["polygon_sha256"] = polygon_fingerprint["sha256"]
    checkpoint_path, checkpoint_id = _checkpoint_path(
        checkpoint_dir, "geometry", identity
    )
    cached = _read_checkpoint(checkpoint_path, "lucas-direct-geometry/v1", identity)
    if cached is not None:
        _require_input_file_signature(polygon_path, polygon_signature)
        return checkpoint_path, checkpoint_id, identity, cached, True

    geometries, polygon_ids, geometry_exclusions, decisions, polygon_rows = (
        _load_direct_polygons(
            polygon_path,
            direct_input["schema"],
            transformer,
            source["geometry_encoding"],
        )
    )
    _require_input_file_signature(polygon_path, polygon_signature)
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
    result = {
        "region": region,
        "source_polygon_records": len(polygon_ids),
        "polygon_ids": sorted(polygon_ids),
        "eligible_reference_polygons": dict(sorted(eligible.items())),
        "eligible_reference_polygons_by_code": dict(
            sorted(Counter(eligible.values()).items())
        ),
        "coverage_outcomes": {
            name: coverage[name] for name in census._COVERAGE_OUTCOMES
        },
        "geometry_exclusions": {
            name: geometry_exclusions[name] for name in census._GEOMETRY_EXCLUSIONS
        },
        "support_scan": {
            "rtree_bbox_candidate_rows": support_rows,
            "accepted_support_rows": support_accepted,
            "rejected_support_rows": support_rows - support_accepted,
            "rejection_reason_counts": dict(sorted(rejections.items())),
        },
        "input_receipt": {
            "source_polygon_records": {
                "name": polygon_relative,
                "row_count": polygon_rows,
            },
            "source_repository_revision": source["input_revision"],
            "lucas_asset_sha256_verified_this_run": lucas_sha256,
        },
        "eligibility_decision_sha256": hashlib.sha256(
            json.dumps(sorted(decisions.items()), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    _write_checkpoint(checkpoint_path, "lucas-direct-geometry/v1", identity, result)
    return checkpoint_path, checkpoint_id, identity, result, False


def _direct_text_file_specs(
    source: dict[str, Any], stream: dict[str, str]
) -> list[tuple[str, str]]:
    direct_input = source["direct_input"]
    schema = direct_input["schema"]
    if schema in {"description-language-v1", "website-polygons-v1"}:
        return [("text_units", direct_input["text_path_template"])]
    return [
        ("polygon_document_links", direct_input["links_path_template"]),
        ("text_units", stream["text_path_template"]),
    ]


def _raw_label_counts(
    label_path: Path,
    key_fields: tuple[str, ...],
    stream_name: str,
    expected_input_revision: str,
) -> dict[str, Any]:
    return census.count_eligible_source_sentences(
        {},
        lambda _columns: iter(()),
        _direct_labels(label_path, key_fields),
        key_fields=key_fields,
        stream_name=stream_name,
        polygon_ids=set(),
        expected_input_revision=expected_input_revision,
    )


def _direct_stream_result(
    *,
    source: dict[str, Any],
    stream_name: str,
    stream: dict[str, str],
    region: str,
    input_root: Path,
    label_root: Path,
    geometry: dict[str, Any],
    geometry_checkpoint_id: str,
    lucas_sha256: str,
    checkpoint_dir: Path | None,
    checkpoint_base: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    direct_input = source["direct_input"]
    polygon_ids = set(geometry["polygon_ids"])
    eligible = geometry["eligible_reference_polygons"]
    label_path, label_relative = _rendered_snapshot_path(
        label_root,
        stream["labels_path_template"],
        region=region,
        stream=stream_name,
    )
    label_available = label_path.is_file()
    text_specs = _direct_text_file_specs(source, stream)
    text_paths = [
        (
            name,
            *_rendered_snapshot_path(
                input_root, template, region=region, stream=stream_name
            ),
        )
        for name, template in text_specs
    ]
    required_text_paths = text_paths if eligible else []
    missing_inputs = [
        relative for _name, path, relative in required_text_paths if not path.is_file()
    ]
    if eligible and not label_available:
        missing_inputs.append(label_relative)

    include_content_hash = checkpoint_dir is not None
    label_fingerprint: dict[str, Any] | None = None
    label_signature: tuple[int, int, int, int, int] | None = None
    if label_available:
        label_fingerprint, label_signature = _input_file_fingerprint(
            label_path, include_content_hash=include_content_hash
        )
    required_text_files = []
    required_text_signatures: list[tuple[Path, tuple[int, int, int, int, int]]] = []
    for _name, path, relative in required_text_paths:
        if not path.is_file():
            continue
        fingerprint, signature = _input_file_fingerprint(
            path, include_content_hash=include_content_hash
        )
        required_text_files.append({"name": relative, **fingerprint})
        required_text_signatures.append((path, signature))

    identity = {
        **checkpoint_base,
        "stage": "labels",
        "region": region,
        "stream": stream_name,
        "geometry_checkpoint_id": geometry_checkpoint_id,
        "label_path": label_relative,
        "label_size_bytes": (
            label_fingerprint["size_bytes"] if label_fingerprint is not None else None
        ),
        "required_text_files": required_text_files,
        "missing_inputs": sorted(missing_inputs),
    }
    if label_fingerprint is not None and "sha256" in label_fingerprint:
        identity["label_sha256"] = label_fingerprint["sha256"]
    checkpoint_path, _ = _checkpoint_path(checkpoint_dir, "results", identity)
    cached = _read_checkpoint(
        checkpoint_path, "lucas-direct-region-result/v1", identity
    )
    if cached is not None:
        if label_signature is not None:
            _require_input_file_signature(label_path, label_signature)
        for path, signature in required_text_signatures:
            _require_input_file_signature(path, signature)
        return cached, True, False

    key_fields = tuple(source["join_key_fields"])
    text_receipt: dict[str, Any]
    label_receipt: dict[str, Any]
    counts: dict[str, Any] | None
    source_label_rows: dict[str, int] | None
    source_label_counts_complete = label_available
    eligible_pool_count_complete = not eligible or not missing_inputs

    if not eligible:
        counts = census.count_eligible_source_sentences(
            {},
            lambda _columns: iter(()),
            _direct_labels(label_path, key_fields) if label_available else (),
            key_fields=key_fields,
            stream_name=stream_name,
            polygon_ids=polygon_ids,
            expected_input_revision=source["input_revision"],
        )
        if label_available and sum(counts["source_label_rows"].values()) != (
            _parquet_row_count(label_path)
        ):
            raise ValueError("source label row count differs from Parquet metadata")
        source_label_rows = counts["source_label_rows"]
        text_receipt = {
            "name": text_specs[-1][1].format(region=region, stream=stream_name),
            "row_count": None,
            "read": False,
            "skipped_reason": "no_eligible_reference_polygons",
        }
        if len(text_specs) > 1:
            text_receipt["polygon_document_links"] = {
                "name": text_specs[0][1].format(region=region, stream=stream_name),
                "row_count": None,
                "read": False,
                "skipped_reason": "no_eligible_reference_polygons",
            }
        label_receipt = {
            "name": label_relative,
            "row_count": sum(source_label_rows.values()) if label_available else None,
            "read": label_available,
            **({} if label_available else {"missing": True}),
        }
    elif missing_inputs:
        counts = None
        source_label_rows = None
        if label_available:
            raw_counts = _raw_label_counts(
                label_path,
                key_fields,
                stream_name,
                source["input_revision"],
            )
            source_label_rows = raw_counts["source_label_rows"]
            if sum(source_label_rows.values()) != _parquet_row_count(label_path):
                raise ValueError("source label row count differs from Parquet metadata")
        source_label_counts_complete = label_available
        eligible_pool_count_complete = False
        text_receipt = {
            "files": [
                {"name": relative, "available": path.is_file(), "read": False}
                for _name, path, relative in text_paths
            ],
            "read": False,
            "missing_inputs": sorted(missing_inputs),
        }
        label_receipt = {
            "name": label_relative,
            "row_count": (
                sum(source_label_rows.values())
                if source_label_rows is not None
                else None
            ),
            "read": label_available,
            **({} if label_available else {"missing": True}),
        }
    else:
        text_factory, text_receipt = _direct_text_units(
            source,
            stream_name,
            input_root,
            region,
            polygon_ids,
            direct_input,
        )
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
        source_label_rows = counts["source_label_rows"]
        label_receipt = {
            "name": label_relative,
            "row_count": sum(source_label_rows.values()),
            "read": True,
        }
        text_receipt["read"] = True

    result = {
        "region": region,
        "stream": stream_name,
        "source_polygon_records": geometry["source_polygon_records"],
        "eligible_reference_polygons_by_code": geometry[
            "eligible_reference_polygons_by_code"
        ],
        "coverage_outcomes": geometry["coverage_outcomes"],
        "geometry_exclusions": geometry["geometry_exclusions"],
        "eligible_source_relevance_counts": counts,
        "eligible_pool_count_complete": eligible_pool_count_complete,
        "source_label_rows": source_label_rows,
        "source_label_counts_complete": source_label_counts_complete,
        "missing_inputs": sorted(missing_inputs),
        "support_scan": geometry["support_scan"],
        "input_receipt": {
            **geometry["input_receipt"],
            "text_units": text_receipt,
            "labels": label_receipt,
            "label_repository_revision": source["label_revision"],
        },
        "eligibility_decision_sha256": geometry["eligibility_decision_sha256"],
        "geometry_checkpoint_id": geometry_checkpoint_id,
    }
    if label_signature is not None:
        _require_input_file_signature(label_path, label_signature)
    for path, signature in required_text_signatures:
        _require_input_file_signature(path, signature)
    if eligible_pool_count_complete:
        _write_checkpoint(
            checkpoint_path,
            "lucas-direct-region-result/v1",
            identity,
            result,
        )
    return (
        result,
        False,
        checkpoint_path is not None and eligible_pool_count_complete,
    )


def _sum_direct_region_results(
    results: list[dict[str, Any]], *, shortfall_scope_complete: bool = False
) -> dict[str, Any]:
    coverage = Counter()
    geometry = Counter()
    labels = Counter()
    joined = Counter()
    eligible = Counter()
    polygons = 0
    incomplete = []
    label_gaps = []
    for result in results:
        coverage.update(result["coverage_outcomes"])
        geometry.update(result["geometry_exclusions"])
        polygons += result["source_polygon_records"]
        if result["source_label_rows"] is not None:
            labels.update(result["source_label_rows"])
        else:
            label_gaps.append({"region": result["region"], "stream": result["stream"]})
        counts = result["eligible_source_relevance_counts"]
        if counts is None:
            incomplete.append(
                {
                    "region": result["region"],
                    "stream": result["stream"],
                    "missing_inputs": result["missing_inputs"],
                }
            )
            continue
        joined.update(counts["joined_to_eligible_polygons"])
        eligible.update(
            counts["unique_eligible_sentence_instances_by_source_relevance"]
        )
        if not result["source_label_counts_complete"]:
            label_gaps.append({"region": result["region"], "stream": result["stream"]})
    return {
        "source_polygon_records": polygons,
        "coverage_outcomes": {
            name: coverage[name] for name in census._COVERAGE_OUTCOMES
        },
        "geometry_exclusions": {
            name: geometry[name] for name in census._GEOMETRY_EXCLUSIONS
        },
        "source_label_rows": {name: labels[name] for name in sorted(labels)},
        "joined_label_rows": {name: joined[name] for name in sorted(joined)},
        "eligible_source_relevance_instances": {
            "yes": eligible["yes"],
            "no": eligible["no"],
        },
        "eligible_pool_counts_complete": not incomplete,
        "source_label_counts_complete": not label_gaps,
        "shortfall_scope_complete": shortfall_scope_complete,
        "incomplete_region_streams": incomplete,
        "source_label_count_gaps": label_gaps,
        "shortfall_from_target": (
            {
                "yes": max(0, 500 - eligible["yes"]),
                "no": max(0, 500 - eligible["no"]),
            }
            if not incomplete and shortfall_scope_complete
            else None
        ),
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
    checkpoint_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a geometry-first census from configured-revision cache paths offline."""
    config = census._load_config(config_path)
    source = census._source_config(config, source_name)
    input_root = _validate_snapshot_root(
        input_snapshot, source["input_repository"], source["input_revision"]
    )
    label_root = _validate_snapshot_root(
        label_snapshot, source["label_repository"], source["label_revision"]
    )
    checkpoint_dir = _validate_checkpoint_directory(
        checkpoint_dir, input_root, label_root
    )
    selected_regions = list(config["scope"]["regions"] if regions is None else regions)
    if not selected_regions or len(selected_regions) != len(set(selected_regions)):
        raise ValueError("requested regions must be non-empty and unique")
    unknown_regions = set(selected_regions) - set(config["scope"]["regions"])
    if unknown_regions:
        raise ValueError(f"regions outside pinned scope: {sorted(unknown_regions)}")
    selected_streams = list(
        [stream["name"] for stream in source["streams"]]
        if stream_names is None
        else stream_names
    )
    if not selected_streams or len(selected_streams) != len(set(selected_streams)):
        raise ValueError("requested streams must be non-empty and unique")
    streams = [census._stream_config(source, name) for name in selected_streams]
    lucas_config = json.loads(lucas_source_config_path.read_text(encoding="utf-8"))
    tool_sha256 = census._file_sha256(Path(__file__))
    census_tool_sha256 = census._file_sha256(Path(census.__file__))
    description_census = importlib.import_module("tools.lucas_description_census")
    description_census_sha256 = census._file_sha256(Path(description_census.__file__))
    coverage_api_sha256 = {
        name: census._file_sha256(
            Path(__file__).resolve().parents[1] / "src/osm_polygon_lucas" / f"{name}.py"
        )
        for name in (
            "lucas_gpkg",
            "lucas_taxonomy",
            "polygon_coverage",
            "support_eligibility",
        )
    }
    package_init_sha256 = census._file_sha256(
        Path(__file__).resolve().parents[1] / "src/osm_polygon_lucas/__init__.py"
    )
    pyproj = importlib.import_module("pyproj")
    pyproj_database = importlib.import_module("pyproj.database")
    runtime_versions = {
        "pyarrow": importlib.import_module("pyarrow").__version__,
        "pyproj": pyproj.__version__,
        "proj": pyproj.proj_version_str,
        "proj_data": pyproj_database.get_database_metadata("PROJ_DATA.VERSION"),
        "epsg_database": pyproj_database.get_database_metadata("EPSG.VERSION"),
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
    }
    checkpoint_code_identity = {
        "direct_source_tool_sha256": tool_sha256,
        "source_pool_census_tool_sha256": census_tool_sha256,
        "description_census_tool_sha256": description_census_sha256,
        "package_init_sha256": package_init_sha256,
        "coverage_api_sha256": coverage_api_sha256,
        "runtime_versions": runtime_versions,
    }
    asset_signature, lucas_sha256, lucas_hash_reused = _verify_or_reuse_lucas_asset(
        gpkg_path,
        lucas_config,
        lucas_source_config_path,
        checkpoint_dir,
        checkpoint_code_identity,
    )
    checkpoint_base = {
        "source_id": source["source_id"],
        "input_repository": source["input_repository"],
        "input_revision": source["input_revision"],
        "label_repository": source["label_repository"],
        "label_revision": source["label_revision"],
        "lucas_sha256": lucas_sha256,
        "source_pool_config_sha256": census._file_sha256(config_path),
        "lucas_config_sha256": census._file_sha256(lucas_source_config_path),
        "code_identity": checkpoint_code_identity,
    }
    transformer = census._projection()
    results_by_stream: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    geometry_checkpoints: dict[str, tuple[Path | None, str, dict[str, Any]]] = {}
    geometry_in_memory: dict[str, dict[str, Any]] = {}
    geometry_reused = geometry_saved = 0
    results_reused = results_saved = 0

    # Complete the spatial pass for every selected region before reading any
    # sentence-unit or label rows. Geometry checkpoints make that pass resumable.
    for region in selected_regions:
        census._require_asset_signature(gpkg_path, asset_signature)
        checkpoint_path, checkpoint_id, identity, geometry, reused = (
            _direct_geometry_checkpoint(
                source=source,
                region=region,
                input_root=input_root,
                gpkg_path=gpkg_path,
                transformer=transformer,
                lucas_sha256=lucas_sha256,
                checkpoint_dir=checkpoint_dir,
                checkpoint_base=checkpoint_base,
            )
        )
        geometry_checkpoints[region] = (checkpoint_path, checkpoint_id, identity)
        if checkpoint_dir is None:
            geometry_in_memory[region] = geometry
        geometry_reused += int(reused)
        geometry_saved += int(checkpoint_path is not None and not reused)
        census._require_asset_signature(gpkg_path, asset_signature)

    selected_scope_complete = _complete_source_scope(
        config, source, selected_regions, selected_streams
    )
    for stream_name, stream in zip(selected_streams, streams, strict=True):
        results = []
        for region in selected_regions:
            census._require_asset_signature(gpkg_path, asset_signature)
            checkpoint_path, checkpoint_id, identity = geometry_checkpoints[region]
            geometry = (
                geometry_in_memory[region]
                if checkpoint_dir is None
                else _read_checkpoint(
                    checkpoint_path, "lucas-direct-geometry/v1", identity
                )
            )
            if geometry is None:
                raise ValueError(f"missing geometry checkpoint for {region}")
            result, reused, saved = _direct_stream_result(
                source=source,
                stream_name=stream_name,
                stream=stream,
                region=region,
                input_root=input_root,
                label_root=label_root,
                geometry=geometry,
                geometry_checkpoint_id=checkpoint_id,
                lucas_sha256=lucas_sha256,
                checkpoint_dir=checkpoint_dir,
                checkpoint_base=checkpoint_base,
            )
            results.append(result)
            results_reused += int(reused)
            results_saved += int(saved)
            census._require_asset_signature(gpkg_path, asset_signature)
        results_by_stream[stream_name] = results
        summaries[stream_name] = _sum_direct_region_results(results)
    all_results = [
        result
        for stream_results in results_by_stream.values()
        for result in stream_results
    ]
    return {
        "report_schema": "lucas-source-pool-census/v4",
        "input_mode": "local-pinned-hf-cache-snapshots",
        "source_name": source_name,
        "source_id": source["source_id"],
        "source_display_label": source.get("display_label", source_name),
        "scope": {
            "reference": config["scope"]["reference"],
            "regions": selected_regions,
            "configured_region_count": len(config["scope"]["regions"]),
            "selected_streams": selected_streams,
            "scope_complete": selected_scope_complete,
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
        "checkpointing": {
            "enabled": checkpoint_dir is not None,
            "directory": str(checkpoint_dir) if checkpoint_dir is not None else None,
            "lucas_asset_hash_reused": lucas_hash_reused,
            "geometry_checkpoints_reused": geometry_reused,
            "geometry_checkpoints_saved": geometry_saved,
            "region_results_reused": results_reused,
            "region_results_saved": results_saved,
        },
        "eligible_pool_counts_complete": all(
            summary["eligible_pool_counts_complete"] for summary in summaries.values()
        ),
        "source_label_counts_complete": all(
            summary["source_label_counts_complete"] for summary in summaries.values()
        ),
        "lucas_asset": {
            "sha256": lucas_sha256,
            "size_bytes": lucas_config["size_bytes"],
            "size_verified_this_run": True,
            "sha256_verified_this_run": not lucas_hash_reused,
            "sha256_verification_checkpoint_reused": lucas_hash_reused,
            "asset_identity_stable_during_scan": True,
        },
        "runtime_versions": {
            **runtime_versions,
        },
        "tool_sha256": tool_sha256,
        "source_pool_census_tool_sha256": census_tool_sha256,
        "description_census_tool_sha256": description_census_sha256,
        "package_init_sha256": package_init_sha256,
        "coverage_api_sha256": coverage_api_sha256,
        "sampling": "not run; eligibility counts are reported before H3 sampling",
        "stream_summaries": summaries,
        "stream_results": results_by_stream,
        "aggregate_across_selected_streams": _sum_direct_region_results(
            all_results,
            shortfall_scope_complete=selected_scope_complete,
        ),
    }
