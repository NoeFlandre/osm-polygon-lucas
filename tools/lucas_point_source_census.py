"""Run a resumable point-based census over local pinned source snapshots."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

import shapely
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry

from osm_polygon_lucas.lucas_point_gpkg import iter_lucas_2022_point_observations
from osm_polygon_lucas.point_coverage import (
    SourcePolygon,
    _taxonomy_level,
    evaluate_point_census,
)
from tools import lucas_source_pool_census as pool_census
from tools.lucas_direct_source_snapshot import (
    _complete_source_scope,
    _direct_labels,
    _direct_text_file_specs,
    _direct_text_units,
    _input_file_fingerprint,
    _parquet_row_count,
    _polygon_id,
    _rendered_snapshot_path,
    _validate_checkpoint_directory,
    _validate_snapshot_root,
    _verify_or_reuse_lucas_asset,
)
from tools.lucas_point_census_domain import (
    PointIndex as _PointIndex,
)
from tools.lucas_point_census_domain import (
    candidate_partition_report,
    coalesce_partition_candidates,
    collect_candidate_links,
    geometry_exclusion,
    join_candidate_labels,
    make_candidate_rows,
    sample_cohort,
    validate_text_key_replay,
)
from tools.lucas_point_census_domain import (
    geometry_digest as _geometry_digest,
)
from tools.lucas_point_census_domain import (
    invalid_geometry_digest as _invalid_geometry_digest,
)
from tools.lucas_point_census_domain import (
    payload_sha256 as _payload_sha256,
)
from tools.lucas_point_census_domain import (
    point_index_from_result as _index_from_point_result,
)
from tools.lucas_point_census_domain import (
    point_result as _point_result,
)
from tools.lucas_point_sampling import (
    _validate_sampling_options,
)

_POINT_SCHEMA = "lucas-point-source-census/v1"
_POINT_CHECKPOINT_SCHEMA = "lucas-point-census-checkpoint/v1"
_CANDIDATE_CHECKPOINT_SCHEMA = "lucas-point-candidate-checkpoint/v1"
_LABEL_STATUSES = {"yes", "no", "skipped", "failed"}
_GEOMETRY_EXCLUSIONS = (
    "invalid_wkb",
    "invalid_geojson",
    "non_polygon_geometry",
    "outside_crs84_bounds",
    "invalid_geometry",
    "zero_area",
)


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    receipt: dict[str, Any]
    signature: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _RunOptions:
    taxonomy_level: str
    target_per_label: int
    seed: int
    cell_size_metres: float
    max_per_cell: int


@dataclass(frozen=True, slots=True)
class _SourceScope:
    config: dict[str, Any]
    source: dict[str, Any]
    source_name: str
    input_root: Path
    label_root: Path
    checkpoint_root: Path
    regions: list[str]
    streams: list[dict[str, str]]
    scope_complete: bool


@dataclass(frozen=True, slots=True)
class _PolygonInputs:
    paths: list[tuple[str, Path, str]]
    fingerprints: list[dict[str, Any]]
    signatures: dict[str, tuple[int, int, int, int, int]]


@dataclass(frozen=True, slots=True)
class _RunContext:
    scope: _SourceScope
    options: _RunOptions
    lucas_config: dict[str, Any]
    runtime_identity: dict[str, Any]
    config_hashes: dict[str, str]
    asset_signature: tuple[int, int, int, int, int]
    lucas_sha256: str
    lucas_hash_reused: bool
    polygon_inputs: _PolygonInputs
    base_identity: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _PointStage:
    result: dict[str, Any]
    index: _PointIndex
    reused: bool


@dataclass(frozen=True, slots=True)
class _CandidateStage:
    cohorts: dict[str, dict[str, Any]]
    input_receipts: dict[str, Any]
    saved: int
    reused: int


@dataclass(frozen=True, slots=True)
class _LucasAsset:
    config: dict[str, Any]
    runtime_identity: dict[str, Any]
    config_hashes: dict[str, str]
    signature: tuple[int, int, int, int, int]
    sha256: str
    hash_reused: bool


@dataclass(frozen=True, slots=True)
class _StreamPartitions:
    results: list[dict[str, Any]]
    receipts: dict[str, Any]
    saved: int
    reused: int


@dataclass(frozen=True, slots=True)
class _CandidatePartition:
    result: dict[str, Any]
    fingerprints: list[dict[str, Any]]
    saved: bool


def _checkpoint_path(
    root: Path,
    stage: str,
    identity: dict[str, Any],
) -> Path:
    digest = _payload_sha256(identity)
    stem = (
        "-".join(
            str(identity[key])
            for key in ("source_id", "region", "stream")
            if identity.get(key) is not None
        )
        or "run"
    )
    return root / stage / f"{stem}-{digest[:16]}.json"


def _read_checkpoint(
    path: Path,
    schema: str,
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    return _validated_checkpoint_result(checkpoint, path, schema, identity)


def _validated_checkpoint_result(
    checkpoint: Any,
    path: Path,
    schema: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    _validate_checkpoint_header(checkpoint, path, schema, identity)
    result = checkpoint.get("result")
    _validate_checkpoint_digest(result, checkpoint, path)
    return result


def _validate_checkpoint_header(
    checkpoint: Any,
    path: Path,
    schema: str,
    identity: dict[str, Any],
) -> None:
    if not isinstance(checkpoint, dict):
        raise ValueError(f"invalid point-census checkpoint at {path}")
    if checkpoint.get("checkpoint_schema") != schema:
        raise ValueError(f"unsupported point-census checkpoint at {path}")
    if checkpoint.get("identity") != identity:
        raise ValueError(f"point-census checkpoint identity mismatch at {path}")


def _validate_checkpoint_digest(
    result: Any, checkpoint: dict[str, Any], path: Path
) -> None:
    if not isinstance(result, dict):
        raise ValueError(f"point-census checkpoint result is invalid at {path}")
    if checkpoint.get("result_sha256") != _payload_sha256(result):
        raise ValueError(f"point-census checkpoint payload digest mismatch at {path}")


def _write_checkpoint(
    path: Path,
    schema: str,
    identity: dict[str, Any],
    result: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "checkpoint_schema": schema,
        "identity": identity,
        "result_sha256": _payload_sha256(result),
        "result": result,
    }
    contents = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = _read_checkpoint(path, schema, identity)
            if existing != result:
                raise ValueError(
                    f"conflicting point-census checkpoint at {path}"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def _runtime_and_code_identity() -> dict[str, Any]:
    pyarrow = importlib.import_module("pyarrow")
    pyproj = importlib.import_module("pyproj")
    pyproj_database = importlib.import_module("pyproj.database")
    source_root = Path(__file__).resolve().parents[1]
    code_paths = {
        "point_census_tool": Path(__file__),
        "point_census_domain": source_root / "tools/lucas_point_census_domain.py",
        "source_pool_tool": Path(pool_census.__file__),
        "direct_snapshot_tool": Path(
            importlib.import_module("tools.lucas_direct_source_snapshot").__file__
        ),
        "point_coverage": source_root / "src/osm_polygon_lucas/point_coverage.py",
        "point_gpkg": source_root / "src/osm_polygon_lucas/lucas_point_gpkg.py",
        "taxonomy": source_root / "src/osm_polygon_lucas/lucas_taxonomy.py",
        "sampling_tool": source_root / "tools/lucas_point_sampling.py",
    }
    return {
        "code_sha256": {
            name: pool_census._file_sha256(path) for name, path in code_paths.items()
        },
        "runtime": {
            "python": platform.python_version(),
            "pyarrow": pyarrow.__version__,
            "pyproj": pyproj.__version__,
            "proj": pyproj.proj_version_str,
            "proj_data": pyproj_database.get_database_metadata("PROJ_DATA.VERSION"),
            "epsg_database": pyproj_database.get_database_metadata("EPSG.VERSION"),
            "shapely": shapely.__version__,
            "geos": shapely.geos_version_string,
        },
    }


def _fingerprint(
    path: Path,
    relative: str,
) -> _Fingerprint:
    receipt, signature = _input_file_fingerprint(path, include_content_hash=True)
    return _Fingerprint({"path": relative, **receipt}, signature)


def _require_signature(path: Path, expected: tuple[int, int, int, int, int]) -> None:
    if not path.is_file() or pool_census._asset_signature(path) != expected:
        raise ValueError(f"pinned source file changed during point census: {path}")


def _region_paths(
    source: dict[str, Any],
    stream: dict[str, str],
    input_root: Path,
    label_root: Path,
    region: str,
) -> list[tuple[str, Path, str]]:
    direct_input = source["direct_input"]
    polygon_path, polygon_relative = _rendered_snapshot_path(
        input_root,
        direct_input["polygon_path_template"],
        region=region,
        stream=stream["name"],
    )
    paths = [("geometry", polygon_path, polygon_relative)]
    for name, template in _direct_text_file_specs(source, stream):
        path, relative = _rendered_snapshot_path(
            input_root, template, region=region, stream=stream["name"]
        )
        paths.append((name, path, relative))
    label_path, label_relative = _rendered_snapshot_path(
        label_root,
        stream["labels_path_template"],
        region=region,
        stream=stream["name"],
    )
    paths.append(("labels", label_path, f"labels:{label_relative}"))
    deduplicated: dict[str, tuple[str, Path, str]] = {}
    for role, path, relative in paths:
        deduplicated.setdefault(str(path.resolve()), (role, path, relative))
    for role, path, relative in deduplicated.values():
        if not path.is_file():
            raise FileNotFoundError(f"pinned {role} file is missing: {relative}")
    return list(deduplicated.values())


def _all_polygon_paths(
    source: dict[str, Any], input_root: Path, regions: list[str]
) -> list[tuple[str, Path, str]]:
    direct_input = source["direct_input"]
    result = []
    for region in regions:
        path, relative = _rendered_snapshot_path(
            input_root,
            direct_input["polygon_path_template"],
            region=region,
            stream="",
        )
        if not path.is_file():
            raise FileNotFoundError(f"pinned geometry file is missing: {relative}")
        result.append((region, path, relative))
    return result


def _polygon_columns(schema: str) -> tuple[str, ...]:
    if schema == "description-language-v1":
        return ("osm_type", "osm_id", "geometry")
    return ("polygon_id", "geometry")


def _decode_source_geometry(
    value: Any, encoding: str
) -> tuple[BaseGeometry | None, str | None]:
    try:
        geometry = pool_census._decode_geometry(value, encoding)
    except (ShapelyError, TypeError, ValueError):
        return None, "invalid_wkb" if encoding == "wkb" else "invalid_geojson"
    exclusion = geometry_exclusion(geometry)
    return (None, exclusion) if exclusion else (geometry, None)


def _load_point_polygons(
    source: dict[str, Any],
    region_paths: list[tuple[str, Path, str]],
) -> tuple[list[SourcePolygon], dict[str, Any]]:
    schema = source["direct_input"]["schema"]
    inventory = _PolygonInventory()
    for _region, path, _relative in region_paths:
        _read_polygon_region(path, schema, source["geometry_encoding"], inventory)
    return inventory.polygons(), inventory.report()


@dataclass(slots=True)
class _PolygonInventory:
    polygons_by_id: dict[str, SourcePolygon] = dataclass_field(default_factory=dict)
    seen_digests: dict[str, str] = dataclass_field(default_factory=dict)
    exclusions: Counter[str] = dataclass_field(default_factory=Counter)
    raw_records: int = 0
    duplicates: int = 0

    def polygons(self) -> list[SourcePolygon]:
        return [self.polygons_by_id[key] for key in sorted(self.polygons_by_id)]

    def report(self) -> dict[str, Any]:
        polygons = self.polygons()
        return {
            "source_polygon_records": self.raw_records,
            "unique_source_polygon_records": len(self.seen_digests),
            "duplicate_polygon_records_coalesced": self.duplicates,
            "valid_unique_source_polygons": len(polygons),
            "geometry_exclusions": {
                name: self.exclusions[name] for name in _GEOMETRY_EXCLUSIONS
            },
        }


def _read_polygon_region(
    path: Path,
    schema: str,
    encoding: str,
    inventory: _PolygonInventory,
) -> None:
    expected_rows = _parquet_row_count(path)
    rows = pool_census._iter_parquet_rows(path, _polygon_columns(schema))
    seen_in_region: set[str] = set()
    count = 0
    for row in rows:
        count += 1
        _register_polygon_row(row, schema, encoding, seen_in_region, inventory)
    if count != expected_rows:
        raise ValueError("source polygon row count differs from Parquet metadata")


def _register_polygon_row(
    row: dict[str, Any],
    schema: str,
    encoding: str,
    seen_in_region: set[str],
    inventory: _PolygonInventory,
) -> None:
    inventory.raw_records += 1
    polygon_id = _polygon_id(row, schema)
    _ensure_region_unique(polygon_id, seen_in_region)
    geometry, exclusion = _decode_source_geometry(row.get("geometry"), encoding)
    digest = _source_geometry_digest(row.get("geometry"), geometry, exclusion)
    _record_polygon(polygon_id, geometry, exclusion, digest, inventory)


def _ensure_region_unique(polygon_id: str, seen_in_region: set[str]) -> None:
    if polygon_id in seen_in_region:
        raise ValueError(
            f"duplicate source polygon identifier {polygon_id!r} in one region"
        )
    seen_in_region.add(polygon_id)


def _source_geometry_digest(
    value: Any, geometry: BaseGeometry | None, exclusion: str | None
) -> str:
    if exclusion is None:
        assert geometry is not None
        return _geometry_digest(geometry)
    return _invalid_geometry_digest(value, exclusion)


def _record_polygon(
    polygon_id: str,
    geometry: BaseGeometry | None,
    exclusion: str | None,
    digest: str,
    inventory: _PolygonInventory,
) -> None:
    previous = inventory.seen_digests.get(polygon_id)
    if previous is not None:
        _record_duplicate_polygon(polygon_id, digest, previous, inventory)
        return
    inventory.seen_digests[polygon_id] = digest
    if exclusion is not None:
        inventory.exclusions[exclusion] += 1
        return
    assert geometry is not None
    inventory.polygons_by_id[polygon_id] = SourcePolygon(polygon_id, geometry)


def _record_duplicate_polygon(
    polygon_id: str,
    digest: str,
    previous: str,
    inventory: _PolygonInventory,
) -> None:
    if previous != digest:
        raise ValueError(f"source polygon {polygon_id!r} has conflicting geometries")
    inventory.duplicates += 1


def _region_polygon_ids(path: Path, schema: str) -> set[str]:
    ids: set[str] = set()
    rows = 0
    for row in pool_census._iter_parquet_rows(path, _polygon_columns(schema)[:-1]):
        rows += 1
        polygon_id = _polygon_id(row, schema)
        if polygon_id in ids:
            raise ValueError(f"duplicate source polygon identifier {polygon_id!r}")
        ids.add(polygon_id)
    if rows != _parquet_row_count(path):
        raise ValueError("source polygon ID count differs from Parquet metadata")
    return ids


def _collect_candidates(
    *,
    source: dict[str, Any],
    stream_name: str,
    region: str,
    source_polygon_ids: set[str],
    point_index: _PointIndex,
    text_factory: Any,
    label_path: Path,
    expected_input_revision: str,
) -> dict[str, Any]:
    key_fields = tuple(source["join_key_fields"])
    candidate_links, text_rows = collect_candidate_links(
        text_factory((*key_fields, "polygon_ids")),
        key_fields,
        source_polygon_ids,
        set(point_index.eligible_polygon_anchors),
        source["source_id"],
    )
    duplicate_validation_rows = 0
    if candidate_links:
        duplicate_validation_rows = validate_text_key_replay(
            text_factory(key_fields),
            key_fields,
            set(candidate_links),
            text_rows,
        )
    labels, source_statuses, joined_statuses = join_candidate_labels(
        _direct_labels(label_path, key_fields),
        key_fields,
        set(candidate_links),
        expected_input_revision,
        _parquet_row_count(label_path),
    )
    candidates = make_candidate_rows(
        source["source_id"], stream_name, region, candidate_links, labels
    )
    return candidate_partition_report(
        region,
        stream_name,
        text_rows,
        duplicate_validation_rows,
        source_statuses,
        joined_statuses,
        candidates,
    )


def _sampling_candidates(
    source: dict[str, Any],
    partition_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    return coalesce_partition_candidates(
        source["direct_input"]["schema"], partition_results
    )


def _sample_cohort(**kwargs: Any) -> dict[str, Any]:
    return sample_cohort(**kwargs)


def _validate_scope(
    config: dict[str, Any],
    source: dict[str, Any],
    regions: list[str] | None,
    stream_names: list[str] | None,
) -> tuple[list[str], list[dict[str, str]], bool]:
    selected_regions = _selected_regions(config, regions)
    selected_streams = _selected_streams(source, stream_names)
    complete = _complete_source_scope(
        config,
        source,
        selected_regions,
        [stream["name"] for stream in selected_streams],
    )
    return selected_regions, selected_streams, complete


def _selected_regions(config: dict[str, Any], regions: list[str] | None) -> list[str]:
    selected = list(config["scope"]["regions"] if regions is None else regions)
    configured = set(config["scope"]["regions"])
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("requested regions must be non-empty and unique")
    unknown = set(selected) - configured
    if unknown:
        raise ValueError(f"regions outside pinned scope: {sorted(unknown)}")
    return selected


def _selected_streams(
    source: dict[str, Any], stream_names: list[str] | None
) -> list[dict[str, str]]:
    selected = _selected_stream_names(source, stream_names)
    return [pool_census._stream_config(source, name) for name in selected]


def _selected_stream_names(
    source: dict[str, Any], stream_names: list[str] | None
) -> list[str]:
    selected = (
        _configured_stream_names(source) if stream_names is None else list(stream_names)
    )
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("requested streams must be non-empty and unique")
    return selected


def _configured_stream_names(source: dict[str, Any]) -> list[str]:
    return [stream["name"] for stream in source["streams"]]


def run_point_source_census(
    input_snapshot: Path,
    label_snapshot: Path,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
    source_name: str,
    *,
    regions: list[str] | None = None,
    stream_names: list[str] | None = None,
    taxonomy_level: str = "main_category",
    target_per_label: int = 500,
    seed: int = 42,
    cell_size_metres: float = 50_000,
    max_per_cell: int = 20,
    checkpoint_dir: Path,
) -> dict[str, Any]:
    """Census point-eligible polygons, join pinned source labels, and sample."""
    scope = _load_source_scope(
        input_snapshot,
        label_snapshot,
        config_path,
        source_name,
        regions,
        stream_names,
        checkpoint_dir,
    )
    options = _validated_run_options(
        scope,
        taxonomy_level,
        target_per_label,
        seed,
        cell_size_metres,
        max_per_cell,
    )
    context = _prepare_run_context(
        scope, options, gpkg_path, config_path, lucas_source_config_path
    )
    point_stage = _run_point_stage(context, gpkg_path)
    candidate_stage = _run_candidate_stage(context, point_stage, gpkg_path)
    pool_census._require_asset_signature(gpkg_path, context.asset_signature)
    return _assemble_report(context, point_stage, candidate_stage)


def _load_source_scope(
    input_snapshot: Path,
    label_snapshot: Path,
    config_path: Path,
    source_name: str,
    regions: list[str] | None,
    stream_names: list[str] | None,
    checkpoint_dir: Path,
) -> _SourceScope:
    config = pool_census._load_config(config_path)
    source = pool_census._source_config(config, source_name)
    input_root = _validate_snapshot_root(
        input_snapshot, source["input_repository"], source["input_revision"]
    )
    label_root = _validate_snapshot_root(
        label_snapshot, source["label_repository"], source["label_revision"]
    )
    checkpoint_root = _validate_checkpoint_directory(
        checkpoint_dir, input_root, label_root
    )
    assert checkpoint_root is not None
    selected_regions, streams, complete = _validate_scope(
        config, source, regions, stream_names
    )
    return _SourceScope(
        config,
        source,
        source_name,
        input_root,
        label_root,
        checkpoint_root,
        selected_regions,
        streams,
        complete,
    )


def _validated_run_options(
    scope: _SourceScope,
    taxonomy_level: str,
    target_per_label: int,
    seed: int,
    cell_size_metres: float,
    max_per_cell: int,
) -> _RunOptions:
    _validate_target_and_seed(target_per_label, seed)
    _validate_stream_sampling(scope, target_per_label, cell_size_metres, max_per_cell)
    _taxonomy_level(taxonomy_level)
    return _RunOptions(
        taxonomy_level, target_per_label, seed, cell_size_metres, max_per_cell
    )


def _validate_target_and_seed(target_per_label: int, seed: int) -> None:
    _validate_target_per_label(target_per_label)
    _validate_seed(seed)


def _validate_target_per_label(target_per_label: int) -> None:
    if (
        isinstance(target_per_label, bool)
        or not isinstance(target_per_label, int)
        or target_per_label < 1
    ):
        raise ValueError("target_per_label must be a positive integer")


def _validate_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")


def _validate_stream_sampling(
    scope: _SourceScope,
    target_per_label: int,
    cell_size_metres: float,
    max_per_cell: int,
) -> None:
    for stream in scope.streams:
        _validate_sampling_options(
            f"{scope.source['source_id']}/{stream['name']}",
            target_per_label,
            cell_size_metres,
            max_per_cell,
        )


def _prepare_run_context(
    scope: _SourceScope,
    options: _RunOptions,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
) -> _RunContext:
    asset = _prepare_lucas_asset(
        scope, options, gpkg_path, config_path, lucas_source_config_path
    )
    polygons = _fingerprint_polygon_inputs(scope)
    identity = _base_checkpoint_identity(scope, options, asset)
    return _RunContext(
        scope,
        options,
        asset.config,
        asset.runtime_identity,
        asset.config_hashes,
        asset.signature,
        asset.sha256,
        asset.hash_reused,
        polygons,
        identity,
    )


def _prepare_lucas_asset(
    scope: _SourceScope,
    options: _RunOptions,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
) -> _LucasAsset:
    lucas_config = json.loads(lucas_source_config_path.read_text(encoding="utf-8"))
    runtime_identity = _runtime_and_code_identity()
    config_hashes = {
        "source_pool_config_sha256": pool_census._file_sha256(config_path),
        "lucas_source_config_sha256": pool_census._file_sha256(
            lucas_source_config_path
        ),
    }
    lucas_identity = {
        **runtime_identity,
        "config_hashes": config_hashes,
        "taxonomy_level": options.taxonomy_level,
    }
    signature, sha256, reused = _verify_or_reuse_lucas_asset(
        gpkg_path,
        lucas_config,
        lucas_source_config_path,
        scope.checkpoint_root,
        lucas_identity,
    )
    return _LucasAsset(
        lucas_config, runtime_identity, config_hashes, signature, sha256, reused
    )


def _fingerprint_polygon_inputs(scope: _SourceScope) -> _PolygonInputs:
    paths = _all_polygon_paths(scope.source, scope.input_root, scope.regions)
    objects = [_fingerprint(path, relative) for _region, path, relative in paths]
    receipts = [
        {"region": region, **fingerprint.receipt}
        for (region, _path, _relative), fingerprint in zip(paths, objects, strict=True)
    ]
    signatures = {
        str(path.resolve()): fingerprint.signature
        for (_region, path, _relative), fingerprint in zip(paths, objects, strict=True)
    }
    return _PolygonInputs(paths, receipts, signatures)


def _base_checkpoint_identity(
    scope: _SourceScope, options: _RunOptions, asset: _LucasAsset
) -> dict[str, Any]:
    source = scope.source
    return {
        "source_id": source["source_id"],
        "source_name": scope.source_name,
        "input_repository": source["input_repository"],
        "input_revision": source["input_revision"],
        "label_repository": source["label_repository"],
        "label_revision": source["label_revision"],
        "geometry_encoding": source["geometry_encoding"],
        "lucas_sha256": asset.sha256,
        "config_hashes": asset.config_hashes,
        "code_identity": asset.runtime_identity,
        "scope": {
            "regions": scope.regions,
            "streams": [stream["name"] for stream in scope.streams],
        },
        "taxonomy_level": options.taxonomy_level,
        "sampling": {
            "target_per_label": options.target_per_label,
            "seed": options.seed,
            "cell_size_metres": options.cell_size_metres,
            "max_per_cell": options.max_per_cell,
        },
    }


def _point_checkpoint_identity(context: _RunContext) -> dict[str, Any]:
    return {
        **context.base_identity,
        "stage": "point-census",
        "polygon_files": context.polygon_inputs.fingerprints,
    }


def _run_point_stage(context: _RunContext, gpkg_path: Path) -> _PointStage:
    identity = _point_checkpoint_identity(context)
    checkpoint_path = _checkpoint_path(context.scope.checkpoint_root, "point", identity)
    result = _read_checkpoint(checkpoint_path, _POINT_CHECKPOINT_SCHEMA, identity)
    reused = result is not None
    if reused:
        _verify_polygon_inputs(context.polygon_inputs)
    else:
        result = _compute_point_result(context, gpkg_path, identity, checkpoint_path)
    return _PointStage(result, _index_from_point_result(result), reused)


def _compute_point_result(
    context: _RunContext,
    gpkg_path: Path,
    identity: dict[str, Any],
    checkpoint_path: Path,
) -> dict[str, Any]:
    polygons, stats = _load_point_polygons(
        context.scope.source, context.polygon_inputs.paths
    )
    _verify_polygon_inputs(context.polygon_inputs)
    pool_census._require_asset_signature(gpkg_path, context.asset_signature)
    census = evaluate_point_census(
        polygons,
        iter_lucas_2022_point_observations(gpkg_path),
        taxonomy_level=context.options.taxonomy_level,
    )
    _verify_polygon_inputs(context.polygon_inputs)
    pool_census._require_asset_signature(gpkg_path, context.asset_signature)
    result = _point_result(census, stats)
    _write_checkpoint(checkpoint_path, _POINT_CHECKPOINT_SCHEMA, identity, result)
    return result


def _verify_polygon_inputs(inputs: _PolygonInputs) -> None:
    for _region, path, _relative in inputs.paths:
        _require_signature(path, inputs.signatures[str(path.resolve())])


def _run_candidate_stage(
    context: _RunContext, point_stage: _PointStage, gpkg_path: Path
) -> _CandidateStage:
    point_index_sha256 = _payload_sha256(point_stage.result["eligible_polygon_anchors"])
    cohorts: dict[str, dict[str, Any]] = {}
    receipts: dict[str, Any] = {
        "geometry": context.polygon_inputs.fingerprints,
        "partitions": {},
    }
    saved = reused = 0
    for stream in context.scope.streams:
        partitions = _run_stream_partitions(
            context, point_stage, stream, point_index_sha256, gpkg_path
        )
        cohorts[stream["name"]] = _sample_cohort(
            source_name=context.scope.source_name,
            source=context.scope.source,
            stream_name=stream["name"],
            partition_results=partitions.results,
            point_index=point_stage.index,
            target_per_label=context.options.target_per_label,
            seed=context.options.seed,
            cell_size_metres=context.options.cell_size_metres,
            max_per_cell=context.options.max_per_cell,
        )
        receipts["partitions"].update(partitions.receipts)
        saved += partitions.saved
        reused += partitions.reused
    return _CandidateStage(cohorts, receipts, saved, reused)


def _run_stream_partitions(
    context: _RunContext,
    point_stage: _PointStage,
    stream: dict[str, str],
    point_index_sha256: str,
    gpkg_path: Path,
) -> _StreamPartitions:
    results = []
    receipts = {}
    saved = reused = 0
    for region in context.scope.regions:
        partition = _load_or_create_candidate_partition(
            context, point_stage, stream, region, point_index_sha256
        )
        results.append(partition.result)
        receipts[f"{context.scope.source['source_id']}/{stream['name']}/{region}"] = (
            partition.fingerprints
        )
        if partition.saved:
            saved += 1
        else:
            reused += 1
        pool_census._require_asset_signature(gpkg_path, context.asset_signature)
    return _StreamPartitions(results, receipts, saved, reused)


def _load_or_create_candidate_partition(
    context: _RunContext,
    point_stage: _PointStage,
    stream: dict[str, str],
    region: str,
    point_index_sha256: str,
) -> _CandidatePartition:
    paths = _region_paths(
        context.scope.source,
        stream,
        context.scope.input_root,
        context.scope.label_root,
        region,
    )
    fingerprint_objects = [
        _fingerprint(path, relative) for _role, path, relative in paths
    ]
    _verify_candidate_geometry_signatures(context, paths, fingerprint_objects)
    fingerprints = [
        {"role": role, **fingerprint.receipt}
        for (role, _path, _relative), fingerprint in zip(
            paths, fingerprint_objects, strict=True
        )
    ]
    signatures = [fingerprint.signature for fingerprint in fingerprint_objects]
    _verify_file_signatures(paths, signatures)
    identity = {
        **context.base_identity,
        "stage": "candidate-join",
        "region": region,
        "stream": stream["name"],
        "point_index_sha256": point_index_sha256,
        "input_files": fingerprints,
    }
    checkpoint_path = _checkpoint_path(
        context.scope.checkpoint_root, "candidates", identity
    )
    result = _read_checkpoint(checkpoint_path, _CANDIDATE_CHECKPOINT_SCHEMA, identity)
    saved = result is None
    if saved:
        result = _create_candidate_partition(
            context, point_stage, stream, region, paths
        )
        _verify_file_signatures(paths, signatures)
        _write_checkpoint(
            checkpoint_path, _CANDIDATE_CHECKPOINT_SCHEMA, identity, result
        )
    else:
        _verify_file_signatures(paths, signatures)
    return _CandidatePartition(result, fingerprints, saved)


def _verify_candidate_geometry_signatures(
    context: _RunContext,
    paths: list[tuple[str, Path, str]],
    fingerprints: list[_Fingerprint],
) -> None:
    for (role, path, _relative), fingerprint in zip(paths, fingerprints, strict=True):
        if role != "geometry":
            continue
        expected_signature = context.polygon_inputs.signatures.get(str(path.resolve()))
        if expected_signature is None:
            raise ValueError(
                f"candidate geometry was not fingerprinted for point census: {path}"
            )
        if fingerprint.signature != expected_signature:
            raise ValueError(f"pinned source file changed during point census: {path}")


def _verify_file_signatures(
    paths: list[tuple[str, Path, str]],
    signatures: list[tuple[int, int, int, int, int]],
) -> None:
    for (_role, path, _relative), signature in zip(paths, signatures, strict=True):
        _require_signature(path, signature)


def _create_candidate_partition(
    context: _RunContext,
    point_stage: _PointStage,
    stream: dict[str, str],
    region: str,
    paths: list[tuple[str, Path, str]],
) -> dict[str, Any]:
    source = context.scope.source
    geometry_path = next(path for role, path, _ in paths if role == "geometry")
    region_ids = _region_polygon_ids(geometry_path, source["direct_input"]["schema"])
    text_factory, _text_receipt = _direct_text_units(
        source,
        stream["name"],
        context.scope.input_root,
        region,
        region_ids,
        source["direct_input"],
    )
    label_path = next(path for role, path, _ in paths if role == "labels")
    return _collect_candidates(
        source=source,
        stream_name=stream["name"],
        region=region,
        source_polygon_ids=region_ids,
        point_index=point_stage.index,
        text_factory=text_factory,
        label_path=label_path,
        expected_input_revision=source["input_revision"],
    )


def _assemble_report(
    context: _RunContext,
    point_stage: _PointStage,
    candidate_stage: _CandidateStage,
) -> dict[str, Any]:
    scope, source, options = (
        context.scope,
        context.scope.source,
        context.options,
    )
    return {
        "report_schema": _POINT_SCHEMA,
        "source_name": scope.source_name,
        "source_id": source["source_id"],
        "source_display_label": source.get("display_label", scope.source_name),
        "scope": {
            "regions": scope.regions,
            "configured_region_count": len(scope.config["scope"]["regions"]),
            "streams": [stream["name"] for stream in scope.streams],
            "scope_complete": scope.scope_complete,
        },
        "method": {
            "point_rule": (
                "actual survey_gps_long/survey_gps_lat observations and "
                f"surveycprnlc mapped to {options.taxonomy_level}; "
                "exactly one distinct "
                "mapped category per polygon"
            ),
            "boundary_outside_multiple_matches": "excluded",
            "conflicting_or_unlabeled_polygons": "excluded",
            "source_yes_no_semantics": (
                "source relevance labels, separate from LUCAS categories"
            ),
            "sampling": {
                "target_per_label": options.target_per_label,
                "seed": options.seed,
                "cell_size_metres": options.cell_size_metres,
                "max_per_cell": options.max_per_cell,
                "anchor_rule": "lexically smallest point-eligible linked polygon ID",
            },
        },
        "source_pins": {
            key: source[key]
            for key in (
                "source_id",
                "input_repository",
                "input_revision",
                "label_repository",
                "label_revision",
                "geometry_encoding",
                "join_key_fields",
            )
        },
        "lucas_asset": {
            "sha256": context.lucas_sha256,
            "size_bytes": context.lucas_config["size_bytes"],
            "sha256_verification_checkpoint_reused": context.lucas_hash_reused,
            "identity_stable_during_run": True,
        },
        "config_hashes": context.config_hashes,
        "code_and_runtime_identity": context.runtime_identity,
        "input_receipts": candidate_stage.input_receipts,
        "point_census": point_stage.result,
        "cohorts": candidate_stage.cohorts,
        "checkpointing": {
            "enabled": True,
            "directory": str(scope.checkpoint_root),
            "point_census_reused": point_stage.reused,
            "candidate_checkpoints_reused": candidate_stage.reused,
            "candidate_checkpoints_saved": candidate_stage.saved,
        },
        "sampling_complete_for_selected_scope": scope.scope_complete,
    }


def _snapshot_under_cache_root(
    cache_root: Path, repository: str, revision: str
) -> Path:
    return (
        cache_root.expanduser()
        / f"datasets--{repository.replace('/', '--')}"
        / "snapshots"
        / revision
    )


def run_all_point_source_censuses(
    cache_root: Path,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
    *,
    regions: list[str] | None = None,
    taxonomy_level: str = "main_category",
    target_per_label: int = 500,
    seed: int = 42,
    cell_size_metres: float = 50_000,
    max_per_cell: int = 20,
    checkpoint_dir: Path,
) -> dict[str, Any]:
    """Run all three configured source schemas from an existing HF cache."""
    config = pool_census._load_config(config_path)
    results = {}
    for source_name, source in config["sources"].items():
        input_snapshot = _snapshot_under_cache_root(
            cache_root, source["input_repository"], source["input_revision"]
        )
        label_snapshot = _snapshot_under_cache_root(
            cache_root, source["label_repository"], source["label_revision"]
        )
        results[source_name] = run_point_source_census(
            input_snapshot,
            label_snapshot,
            gpkg_path,
            config_path,
            lucas_source_config_path,
            source_name,
            regions=regions,
            taxonomy_level=taxonomy_level,
            target_per_label=target_per_label,
            seed=seed,
            cell_size_metres=cell_size_metres,
            max_per_cell=max_per_cell,
            checkpoint_dir=checkpoint_dir,
        )
    return {
        "report_schema": _POINT_SCHEMA,
        "source_runs": results,
        "scope_complete": all(
            result["scope"]["scope_complete"] for result in results.values()
        ),
    }


def _write_new_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )


def _validate_separate_path(
    path: Path, protected_roots: Iterable[Path], label: str
) -> None:
    resolved = path.expanduser().resolve(strict=False)
    for protected in protected_roots:
        root = protected.expanduser().resolve(strict=False)
        if resolved == root or resolved in root.parents or root in resolved.parents:
            raise ValueError(f"{label} must be separate from protected path {root}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hf-cache-root", type=Path)
    parser.add_argument("--source-snapshot", type=Path)
    parser.add_argument("--label-snapshot", type=Path)
    parser.add_argument("--lucas-gpkg", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=pool_census._CONFIG_PATH)
    parser.add_argument(
        "--lucas-source-config", type=Path, default=pool_census._LUCAS_CONFIG_PATH
    )
    parser.add_argument(
        "--source", choices=("Description", "Website", "Wikidata", "all"), required=True
    )
    parser.add_argument("--region", action="append", dest="regions")
    parser.add_argument("--stream", action="append", dest="streams")
    parser.add_argument("--taxonomy-level", default="main_category")
    parser.add_argument("--target-per-label", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cell-size-metres", type=float, default=50_000)
    parser.add_argument("--max-per-cell", type=int, default=20)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _validate_cli_args(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if args.output.exists():
        parser.error(f"refusing to overwrite existing report: {args.output}")
    _validate_source_arguments(args, parser)
    protected_roots = _protected_roots(args)
    _validate_cli_paths(args, parser, protected_roots)


def _validate_source_arguments(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if args.source == "all":
        _validate_all_source_arguments(args, parser)
    else:
        _validate_single_source_arguments(args, parser)


def _validate_all_source_arguments(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if args.hf_cache_root is None:
        parser.error("--source all requires --hf-cache-root")
    if args.source_snapshot or args.label_snapshot:
        parser.error("--source all requires --hf-cache-root only")
    if args.streams:
        parser.error("--stream cannot be applied to all source schemas")


def _validate_single_source_arguments(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    _reject_mixed_snapshot_options(args, parser)
    _require_single_snapshot_source(args, parser)


def _reject_mixed_snapshot_options(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if args.hf_cache_root is not None and (args.source_snapshot or args.label_snapshot):
        parser.error("choose --hf-cache-root or explicit snapshots")


def _require_single_snapshot_source(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    has_explicit_snapshots = bool(args.source_snapshot and args.label_snapshot)
    if args.hf_cache_root is None and not has_explicit_snapshots:
        parser.error(
            "provide --hf-cache-root or both --source-snapshot and --label-snapshot"
        )


def _protected_roots(args: argparse.Namespace) -> list[Path]:
    if args.hf_cache_root is not None:
        return [args.hf_cache_root]
    return [path for path in (args.source_snapshot, args.label_snapshot) if path]


def _validate_cli_paths(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    protected_roots: list[Path],
) -> None:
    try:
        _validate_separate_path(args.output, protected_roots, "report output")
        _validate_separate_path(
            args.checkpoint_dir, protected_roots, "checkpoint directory"
        )
        _validate_separate_path(
            args.output,
            [args.checkpoint_dir],
            "report output",
        )
    except ValueError as exc:
        parser.error(str(exc))


def _run_cli(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.source == "all":
        report = run_all_point_source_censuses(
            args.hf_cache_root,
            args.lucas_gpkg,
            args.config,
            args.lucas_source_config,
            regions=args.regions,
            taxonomy_level=args.taxonomy_level,
            target_per_label=args.target_per_label,
            seed=args.seed,
            cell_size_metres=args.cell_size_metres,
            max_per_cell=args.max_per_cell,
            checkpoint_dir=args.checkpoint_dir,
        )
    else:
        input_snapshot, label_snapshot = _resolve_single_source_snapshots(args)
        report = run_point_source_census(
            input_snapshot,
            label_snapshot,
            args.lucas_gpkg,
            args.config,
            args.lucas_source_config,
            args.source,
            regions=args.regions,
            stream_names=args.streams,
            taxonomy_level=args.taxonomy_level,
            target_per_label=args.target_per_label,
            seed=args.seed,
            cell_size_metres=args.cell_size_metres,
            max_per_cell=args.max_per_cell,
            checkpoint_dir=args.checkpoint_dir,
        )
    _write_cli_report(args.output, report, parser)


def _resolve_single_source_snapshots(
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    if args.hf_cache_root is not None:
        config = pool_census._load_config(args.config)
        source = pool_census._source_config(config, args.source)
        return (
            _snapshot_under_cache_root(
                args.hf_cache_root,
                source["input_repository"],
                source["input_revision"],
            ),
            _snapshot_under_cache_root(
                args.hf_cache_root,
                source["label_repository"],
                source["label_revision"],
            ),
        )
    return args.source_snapshot, args.label_snapshot


def _write_cli_report(
    output_path: Path, report: dict[str, Any], parser: argparse.ArgumentParser
) -> None:
    try:
        _write_new_json(output_path, report)
    except FileExistsError:
        parser.error(f"refusing to overwrite existing report: {output_path}")
    print(
        json.dumps(
            {"report": str(output_path), "report_schema": report["report_schema"]}
        )
    )


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_cli_args(args, parser)
    _run_cli(args, parser)


if __name__ == "__main__":
    main()
