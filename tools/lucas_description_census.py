"""Bounded, read-only LUCAS eligibility census for Austria and Belgium."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely
from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from osm_polygon_lucas import (
    LUCAS_C3_LEVELS,
    EligibleSupport,
    LevelStatus,
    Rejection,
    assess_osm_polygon,
    iter_lucas_2022_supports,
)

_REGIONS = ("austria", "belgium")
_KEY_FIELDS = ("description_identity", "tag_key", "sentence_index")
_BINARY_SOURCE_LABELS = {"yes", "no"}
_ELIGIBLE_REFERENCE_CODES = frozenset(LUCAS_C3_LEVELS[0].labels.values())


@dataclass(frozen=True, slots=True)
class CoverageDecision:
    """Spatial eligibility and an optional diagnostic candidate reference code."""

    outcome: str
    reference_code: str | None = None
    fraction: float | None = None
    support_class_codes: tuple[str, ...] = ()


def _occurrence_key(row: dict[str, Any]) -> tuple[str, str, int]:
    identity, tag_key, sentence_index = (row.get(name) for name in _KEY_FIELDS)
    if not isinstance(identity, str) or not identity:
        raise ValueError("Description occurrence identity must be non-empty text")
    if not isinstance(tag_key, str) or not tag_key:
        raise ValueError("Description occurrence tag_key must be non-empty text")
    if (
        isinstance(sentence_index, bool)
        or not isinstance(sentence_index, int)
        or sentence_index < 0
    ):
        raise ValueError("Description occurrence sentence_index must be non-negative")
    return identity, tag_key, sentence_index


def coverage_decision(coverage: Any) -> CoverageDecision:
    """Classify full-area LUCAS main-category evidence without inventing labels."""
    [main] = [level for level in coverage.levels if level.level == "main_category"]
    if main.status is LevelStatus.WINNER:
        assert main.winner is not None
        winner = next(item for item in main.classes if item.label == main.winner)
        class_codes = tuple(
            sorted(
                {
                    support.raw_class
                    for support in coverage.supports
                    if support.intersection_area > 0
                    and LUCAS_C3_LEVELS[0].labels.get(support.raw_class) == main.winner
                }
            )
        )
        return CoverageDecision("eligible", main.winner, winner.fraction, class_codes)
    if main.status is LevelStatus.AMBIGUOUS:
        return CoverageDecision("ambiguous")

    positive = [
        support for support in coverage.supports if support.intersection_area > 0
    ]
    if not positive:
        return CoverageDecision("boundary_only" if coverage.supports else "no_overlap")

    labels = LUCAS_C3_LEVELS[0].labels
    if any(support.raw_class not in labels for support in positive):
        return CoverageDecision("unknown_unmapped_support")

    candidate = min(main.classes, key=lambda item: (-item.fraction, item.label))
    class_codes = tuple(
        sorted(
            {
                support.raw_class
                for support in positive
                if labels.get(support.raw_class) == candidate.label
            }
        )
    )
    return CoverageDecision(
        "below_threshold", candidate.label, candidate.fraction, class_codes
    )


def count_eligible_sentences(
    eligible_polygon_reference_codes: dict[str, str],
    text_units: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    *,
    polygon_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Join source labels only to text units linked to spatially eligible polygons."""
    if set(eligible_polygon_reference_codes.values()) - _ELIGIBLE_REFERENCE_CODES:
        raise ValueError("eligible polygons must have LUCAS main-category codes")

    source_statuses: Counter[str] = Counter()
    coverage_by_key: dict[tuple[str, str, int], set[str]] = {}
    seen_text_keys: set[tuple[str, str, int]] = set()
    dangling_links = 0
    for row in text_units:
        key = _occurrence_key(row)
        if key in seen_text_keys:
            raise ValueError(f"duplicate Description text-unit key: {key!r}")
        seen_text_keys.add(key)
        linked = row.get("polygon_ids")
        if not isinstance(linked, list):
            raise ValueError("Description polygon_ids must be a list")
        if polygon_ids is not None:
            dangling_links += sum(
                polygon_id not in polygon_ids for polygon_id in linked
            )
        outcomes = {
            eligible_polygon_reference_codes[polygon_id]
            for polygon_id in linked
            if polygon_id in eligible_polygon_reference_codes
        }
        if outcomes:
            coverage_by_key[key] = outcomes

    if dangling_links:
        raise ValueError(
            f"Description text units reference {dangling_links} unknown polygons"
        )

    labels_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in labels:
        status = row.get("status")
        if not isinstance(status, str):
            raise ValueError("Description label status must be text")
        source_statuses[status] += 1
        if not coverage_by_key:
            continue
        key = _occurrence_key(row)
        if key not in coverage_by_key:
            continue
        if key in labels_by_key:
            raise ValueError(f"duplicate eligible Description label key: {key!r}")
        labels_by_key[key] = row

    by_reference: dict[str, dict[str, Any]] = {}
    joined_statuses: Counter[str] = Counter()
    decision_rows: list[tuple[tuple[str, str, int], str, str]] = []
    for key, reference_codes in coverage_by_key.items():
        label = labels_by_key.get(key)
        if label is None:
            raise ValueError("eligible Description text unit has no pinned label row")
        status = label["status"]
        joined_statuses[status] += 1
        for reference_code in sorted(reference_codes):
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
            if status in _BINARY_SOURCE_LABELS:
                bucket[f"source_relevance_{status}"].add(key)
                decision_rows.append((key, reference_code, status))
            else:
                bucket["non_binary_statuses"][status] += 1
                decision_rows.append((key, reference_code, f"excluded:{status}"))

    union_yes = set().union(
        *(bucket["source_relevance_yes"] for bucket in by_reference.values())
    )
    union_no = set().union(
        *(bucket["source_relevance_no"] for bucket in by_reference.values())
    )
    multi_reference_instances = sum(
        len(reference_codes) > 1 for reference_codes in coverage_by_key.values()
    )

    by_reference_result: dict[str, Any] = {}
    for reference_code, bucket in sorted(by_reference.items()):
        by_reference_result[reference_code] = {
            "linked_text_unit_rows": bucket["linked_text_unit_rows"],
            "source_relevance_yes": len(bucket["source_relevance_yes"]),
            "source_relevance_no": len(bucket["source_relevance_no"]),
            "non_binary_statuses": dict(sorted(bucket["non_binary_statuses"].items())),
        }

    return {
        "by_lucas_reference_code": by_reference_result,
        "unique_eligible_sentence_instances_by_source_relevance": {
            "yes": len(union_yes),
            "no": len(union_no),
        },
        "text_units_linked_to_multiple_reference_codes": multi_reference_instances,
        "source_label_rows": dict(sorted(source_statuses.items())),
        "joined_to_eligible_polygons": dict(sorted(joined_statuses.items())),
        "not_joined_to_eligible_polygons": {
            status: count - joined_statuses[status]
            for status, count in sorted(source_statuses.items())
        },
        "eligible_sentence_decision_sha256": _decision_digest(decision_rows),
    }


def _decision_digest(
    rows: list[tuple[tuple[str, str, int], str, str]],
) -> str:
    encoded = json.dumps(sorted(rows), ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _status_counts_match_manifest(
    observed: dict[str, int], expected: dict[str, int]
) -> bool:
    """Compare positive status counts while treating omitted zeroes as zero."""
    observed_nonzero = {status: count for status, count in observed.items() if count}
    expected_nonzero = {status: count for status, count in expected.items() if count}
    return observed_nonzero == expected_nonzero


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _require_asset_signature(
    path: Path, expected: tuple[int, int, int, int, int]
) -> None:
    if not path.is_file() or _asset_signature(path) != expected:
        raise ValueError("LUCAS GeoPackage changed during the census")


def _verify_lucas_asset(path: Path, config: dict[str, Any]) -> str:
    """Verify the supplied GeoPackage against its pinned size and SHA-256."""
    if not path.is_file():
        raise FileNotFoundError(path)
    signature = _asset_signature(path)
    if signature[2] != config["size_bytes"]:
        raise ValueError(
            "LUCAS GeoPackage size differs from the verified source config"
        )
    actual_sha256 = _file_sha256(path)
    _require_asset_signature(path, signature)
    if actual_sha256 != config["sha256"]:
        raise ValueError(
            "LUCAS GeoPackage SHA-256 differs from the verified source config"
        )
    return actual_sha256


def _verified_table_file(
    root: Path, manifest: dict[str, Any], table: str
) -> tuple[Path, dict[str, Any]]:
    entry = manifest["outputs"][table]
    path = root / entry["name"]
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != entry["size_bytes"]:
        raise ValueError(f"{path.name} size does not match its pinned manifest")
    actual = _file_sha256(path)
    if actual != entry["sha256"]:
        raise ValueError(f"{path.name} SHA-256 does not match its pinned manifest")
    return path, entry


def _read_parquet(path: Path, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "install PyArrow and pyproj in the census environment"
        ) from exc
    return pq.read_table(path, columns=list(columns)).to_pylist()


def _projection() -> Any:
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError(
            "install PyArrow and pyproj in the census environment"
        ) from exc
    return Transformer.from_crs("OGC:CRS84", "EPSG:3035", always_xy=True)


def _load_polygons(
    root: Path,
    manifest: dict[str, Any],
    transformer: Any,
) -> tuple[list[tuple[str, BaseGeometry]], set[str], Counter[str], dict[str, str]]:
    path, entry = _verified_table_file(root, manifest, "polygon_records")
    rows = _read_parquet(path, ("polygon_id", "geometry"))
    if len(rows) != entry["row_count"]:
        raise ValueError("polygon_records row count does not match its manifest")

    seen: set[str] = set()
    geometries: list[tuple[str, BaseGeometry]] = []
    decisions: dict[str, str] = {}
    exclusions: Counter[str] = Counter()
    for row in rows:
        polygon_id = row["polygon_id"]
        if not isinstance(polygon_id, str) or not polygon_id:
            raise ValueError("polygon_records contains a missing polygon_id")
        if polygon_id in seen:
            raise ValueError(f"duplicate polygon_id {polygon_id!r}")
        seen.add(polygon_id)
        try:
            geometry = shapely.from_wkb(row["geometry"])
        except (ShapelyError, TypeError, ValueError):
            exclusions["invalid_wkb"] += 1
            decisions[polygon_id] = "invalid_wkb"
            continue
        if not isinstance(geometry, Polygon | MultiPolygon):
            exclusions["non_polygon_geometry"] += 1
            decisions[polygon_id] = "non_polygon_geometry"
            continue
        minx, miny, maxx, maxy = geometry.bounds
        if minx < -180 or miny < -90 or maxx > 180 or maxy > 90:
            raise ValueError(
                f"{polygon_id}: Description geometry is outside CRS84 bounds"
            )
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

    if not geometries:
        raise ValueError("no valid Description polygon geometry available")
    return geometries, seen, exclusions, decisions


def _bounds(
    geometries: list[tuple[str, BaseGeometry]],
) -> tuple[float, float, float, float]:
    minx = min(geometry.bounds[0] for _, geometry in geometries)
    miny = min(geometry.bounds[1] for _, geometry in geometries)
    maxx = max(geometry.bounds[2] for _, geometry in geometries)
    maxy = max(geometry.bounds[3] for _, geometry in geometries)
    return minx, miny, maxx, maxy


def _census_polygons(
    geometries: list[tuple[str, BaseGeometry]],
    bounds: tuple[float, float, float, float],
    gpkg_path: Path,
    initial_decisions: dict[str, str],
) -> tuple[
    dict[str, str],
    Counter[str],
    Counter[str],
    int,
    int,
    dict[str, dict[str, Any]],
    dict[str, Any] | None,
]:
    supports: list[EligibleSupport] = []
    support_rejections: Counter[str] = Counter()
    support_rows = 0
    support_accepted = 0
    for result in iter_lucas_2022_supports(gpkg_path, bounds=bounds):
        support_rows += 1
        if isinstance(result, EligibleSupport):
            supports.append(result)
            support_accepted += 1
        elif isinstance(result, Rejection):
            support_rejections.update(str(reason) for reason in result.reasons)
        else:
            raise TypeError("unexpected LUCAS loader result")

    tree = STRtree([support.geometry for support in supports]) if supports else None
    eligible: dict[str, str] = {}
    outcomes: Counter[str] = Counter()
    decisions: dict[str, dict[str, Any]] = {
        polygon_id: {"outcome": outcome}
        for polygon_id, outcome in initial_decisions.items()
    }
    below_threshold_examples: list[tuple[float, str, str, tuple[str, ...]]] = []
    for polygon_id, geometry in geometries:
        indices = tree.query(geometry) if tree is not None else ()
        candidates = [supports[int(index)] for index in indices]
        result = assess_osm_polygon(geometry, candidates, LUCAS_C3_LEVELS)
        decision = coverage_decision(result)
        decisions[polygon_id] = {
            "outcome": decision.outcome,
            "reference_code": decision.reference_code,
            "coverage_fraction": decision.fraction,
            "support_class_codes": list(decision.support_class_codes),
        }
        outcomes[decision.outcome] += 1
        if decision.outcome == "eligible":
            assert decision.reference_code is not None
            eligible[polygon_id] = decision.reference_code
        elif decision.outcome == "below_threshold":
            assert decision.reference_code is not None
            assert decision.fraction is not None
            below_threshold_examples.append(
                (
                    decision.fraction,
                    polygon_id,
                    decision.reference_code,
                    decision.support_class_codes,
                )
            )
    best_below_threshold = None
    if below_threshold_examples:
        fraction, polygon_id, reference_code, class_codes = min(
            below_threshold_examples, key=lambda row: (-row[0], row[1])
        )
        best_below_threshold = {
            "polygon_id": polygon_id,
            "candidate_reference_code": reference_code,
            "coverage_fraction": fraction,
            "threshold": 0.8,
            "support_raw_class_codes": list(class_codes),
        }
    return (
        eligible,
        outcomes,
        support_rejections,
        support_rows,
        support_accepted,
        decisions,
        best_below_threshold,
    )


def _read_sentence_counts(
    root: Path,
    manifest: dict[str, Any],
    eligible: dict[str, str],
    polygon_ids: set[str],
    expected_input_revision: str,
) -> dict[str, Any]:
    text_path, text_entry = _verified_table_file(root, manifest, "text_units")
    label_path, label_entry = _verified_table_file(root, manifest, "labels")
    text_units = _read_parquet(
        text_path,
        ("description_identity", "tag_key", "sentence_index", "polygon_ids"),
    )
    labels = _read_parquet(
        label_path,
        (
            "description_identity",
            "tag_key",
            "sentence_index",
            "status",
            "input_revision",
        ),
    )
    if (
        len(text_units) != text_entry["row_count"]
        or len(labels) != label_entry["row_count"]
    ):
        raise ValueError(
            "sentence tables do not match their pinned manifest row counts"
        )
    input_revisions = {row["input_revision"] for row in labels}
    if input_revisions != {expected_input_revision}:
        raise ValueError("Description label table uses an unexpected input revision")

    result = count_eligible_sentences(
        eligible,
        text_units,
        labels,
        polygon_ids=polygon_ids,
    )
    manifest_statuses = manifest.get("status_counts", {})
    if not _status_counts_match_manifest(
        result["source_label_rows"], manifest_statuses
    ):
        raise ValueError("sentence label status totals do not match the manifest")

    result["input_files"] = {
        "labels": {"name": label_path.name, "sha256": label_entry["sha256"]},
        "text_units": {"name": text_path.name, "sha256": text_entry["sha256"]},
    }
    return result


def _manifest_for_region(root: Path, region: str) -> tuple[Path, dict[str, Any]]:
    stem = f"description__labels-language-v1-data-{region}-latest"
    path = root / f"{stem}.manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return path, manifest


def _run_region(
    atlas_root: Path,
    gpkg_path: Path,
    region: str,
    source_pin: dict[str, Any],
    lucas_sha256: str,
    transformer: Any,
) -> dict[str, Any]:
    manifest_path, manifest = _manifest_for_region(atlas_root, region)
    if manifest["repo_id"] != source_pin["label_repository"]:
        raise ValueError(f"{region}: unexpected Description label repository")
    if manifest["revision"] != source_pin["label_revision"]:
        raise ValueError(f"{region}: unexpected Description label revision")
    if manifest["source_id"] != "description":
        raise ValueError(f"{region}: unexpected source_id")

    geometries, all_polygon_ids, geometry_exclusions, decisions = _load_polygons(
        atlas_root, manifest, transformer
    )
    bounds = _bounds(geometries)
    (
        eligible,
        spatial_outcomes,
        support_rejections,
        support_rows,
        support_accepted,
        decisions,
        best_below_threshold,
    ) = _census_polygons(geometries, bounds, gpkg_path, decisions)
    sentence_counts = _read_sentence_counts(
        atlas_root,
        manifest,
        eligible,
        all_polygon_ids,
        source_pin["input_revision"],
    )
    polygon_decisions = sorted(decisions.items())
    polygon_digest = hashlib.sha256(
        json.dumps(polygon_decisions, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    polygon_entry = manifest["outputs"]["polygon_records"]
    return {
        "source_polygon_records": len(all_polygon_ids),
        "eligible_reference_polygons_by_code": dict(
            sorted(Counter(eligible.values()).items())
        ),
        "best_below_threshold_example": best_below_threshold,
        "coverage_outcomes": {
            outcome: spatial_outcomes[outcome]
            for outcome in (
                "eligible",
                "below_threshold",
                "ambiguous",
                "unknown_unmapped_support",
                "no_overlap",
                "boundary_only",
            )
        },
        "geometry_exclusions": {
            reason: geometry_exclusions[reason]
            for reason in (
                "invalid_wkb",
                "non_polygon_geometry",
                "invalid_geometry",
                "zero_area",
                "invalid_after_projection",
                "zero_area_after_projection",
            )
        },
        "eligible_source_relevance_counts": sentence_counts,
        "support_scan": {
            "rtree_bbox_candidate_rows": support_rows,
            "accepted_support_rows": support_accepted,
            "rejected_support_rows": support_rows - support_accepted,
            "rejection_reason_counts": dict(sorted(support_rejections.items())),
            "epsg3035_candidate_bounds": [float(value) for value in bounds],
        },
        "input_receipt": {
            "atlas_manifest": {
                "name": manifest_path.name,
                "sha256": _file_sha256(manifest_path),
                "label_repository_revision": manifest["revision"],
                "source_input_revision": source_pin["input_revision"],
            },
            "polygon_records": {
                "name": polygon_entry["name"],
                "sha256": polygon_entry["sha256"],
            },
            "lucas_asset_sha256_verified_this_run": lucas_sha256,
        },
        "eligibility_decision_sha256": polygon_digest,
    }


def run_census(
    atlas_root: Path,
    gpkg_path: Path,
    config_path: Path,
    lucas_source_config_path: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_pin = config["sources"]["Description"]
    lucas_config = json.loads(lucas_source_config_path.read_text(encoding="utf-8"))
    asset_signature = _asset_signature(gpkg_path)
    lucas_sha256 = _verify_lucas_asset(gpkg_path, lucas_config)
    _require_asset_signature(gpkg_path, asset_signature)

    transformer = _projection()
    pyarrow = importlib.import_module("pyarrow")
    pyproj = importlib.import_module("pyproj")
    package_dir = Path(__file__).resolve().parents[1] / "src/osm_polygon_lucas"
    regions = {}
    for region in _REGIONS:
        _require_asset_signature(gpkg_path, asset_signature)
        regions[region] = _run_region(
            atlas_root,
            gpkg_path,
            region,
            source_pin,
            lucas_sha256,
            transformer,
        )
        _require_asset_signature(gpkg_path, asset_signature)
    return {
        "receipt_schema": "lucas-description-bounded-census/v3",
        "method": {
            "regions": list(_REGIONS),
            "source_geometry_crs": "OGC:CRS84",
            "support_geometry_crs": "EPSG:3035",
            "projection": "OGC:CRS84 to EPSG:3035, always_xy=True",
            "coverage_level": "main_category",
            "minimum_class_fraction": 0.8,
            "denominator": "full projected OSM polygon area, not clipped",
            "eligible_reference_code": (
                "exactly one main_category reaches >= 0.80; use that category code"
            ),
            "below_threshold": (
                "no class reaches 0.80; exclude polygon and its linked source labels"
            ),
            "source_label_semantics": (
                "existing yes/no values are source relevance labels, "
                "separate from LUCAS reference codes"
            ),
            "excluded": [
                "ambiguous",
                "unknown_unmapped_support",
                "no_overlap",
                "boundary_only",
                "invalid_wkb",
                "non_polygon_geometry",
                "invalid_geometry",
                "zero_area",
                "invalid_after_projection",
                "zero_area_after_projection",
                "failed and skipped source labels",
            ],
            "sentence_join_key": list(_KEY_FIELDS),
            "label_join_stage": "post-eligibility; eligible-polygon links only",
            "sampling": "none; census counts only",
        },
        "tool_sha256": _file_sha256(Path(__file__)),
        "coverage_api_sha256": {
            name: _file_sha256(package_dir / f"{name}.py")
            for name in (
                "lucas_gpkg",
                "lucas_taxonomy",
                "polygon_coverage",
                "support_eligibility",
            )
        },
        "runtime_versions": {
            "pyarrow": pyarrow.__version__,
            "pyproj": pyproj.__version__,
            "shapely": shapely.__version__,
        },
        "source_pins": source_pin,
        "lucas_asset": {
            "size_bytes": lucas_config["size_bytes"],
            "sha256": lucas_sha256,
            "sha256_status": lucas_config["sha256_status"],
            "size_verified_this_run": True,
            "sha256_verified_this_run": True,
            "asset_identity_stable_during_scan": True,
        },
        "regions": regions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-root", required=True, type=Path)
    parser.add_argument("--lucas-gpkg", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config/cohort-sources.json",
    )
    parser.add_argument(
        "--lucas-source-config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config/lucas-2022-source.json",
    )
    args = parser.parse_args()
    receipt = run_census(
        args.atlas_root, args.lucas_gpkg, args.config, args.lucas_source_config
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
