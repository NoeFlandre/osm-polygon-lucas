"""Command-line coverage report for one OSM polygon."""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import shapely
from shapely.errors import ShapelyError

from osm_polygon_lucas.lucas_gpkg import iter_lucas_2022_supports
from osm_polygon_lucas.lucas_taxonomy import LUCAS_C3_LEVELS
from osm_polygon_lucas.polygon_coverage import (
    MIN_CLASS_FRACTION,
    LevelDecision,
    PolygonCoverage,
    SupportEvidence,
    assess_osm_polygon,
    validate_osm_polygon,
)
from osm_polygon_lucas.support_eligibility import (
    Coordinates,
    EligibleSupport,
    Rejection,
)


@dataclass(frozen=True, slots=True)
class _SourceSelection:
    eligible: tuple[EligibleSupport, ...]
    rejections: tuple[Rejection, ...]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        output = _report_text(args)
        _write_output(args.output, output)
    except (OSError, ValueError, ShapelyError) as exc:
        parser.error(str(exc))
    return 0


def _report_text(args: argparse.Namespace) -> str:
    wkt_bytes, osm_polygon = _read_osm_wkt(args.osm_wkt)
    selection = _load_source_selection(args.lucas_gpkg, osm_polygon.bounds)
    coverage = assess_osm_polygon(osm_polygon, selection.eligible, LUCAS_C3_LEVELS)
    payload = _report(
        wkt_bytes,
        args.osm_crs,
        _source_manifest(),
        osm_polygon.bounds,
        selection,
        coverage,
    )
    return json.dumps(payload, allow_nan=False, sort_keys=True, indent=2) + "\n"


def _load_source_selection(
    gpkg_path: str, bounds: tuple[float, float, float, float]
) -> _SourceSelection:
    loaded = tuple(iter_lucas_2022_supports(gpkg_path, bounds=bounds))
    return _SourceSelection(
        eligible=tuple(item for item in loaded if isinstance(item, EligibleSupport)),
        rejections=tuple(item for item in loaded if isinstance(item, Rejection)),
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lucas-cover",
        description="Measure LUCAS 2022 class coverage for one EPSG:3035 OSM polygon.",
    )
    parser.add_argument(
        "--osm-wkt",
        required=True,
        help="UTF-8 WKT file containing one polygon, or '-' to read stdin.",
    )
    parser.add_argument(
        "--osm-crs",
        required=True,
        choices=("EPSG:3035",),
        help="CRS of the supplied WKT. The LUCAS source uses EPSG:3035.",
    )
    parser.add_argument(
        "--lucas-gpkg",
        required=True,
        help="Path to the pinned LUCAS 2022 Copernicus GeoPackage.",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="JSON output path, or '-' for stdout (default).",
    )
    return parser


def _read_osm_wkt(path: str) -> tuple[bytes, shapely.Geometry]:
    raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
    text = raw.decode("utf-8")
    if not text.strip():
        raise ValueError("OSM WKT input is empty")
    try:
        geometry = shapely.from_wkt(text)
    except ShapelyError as exc:
        raise ValueError(f"invalid OSM WKT: {exc}") from exc
    return raw, validate_osm_polygon(geometry)


def _source_manifest() -> dict[str, Any]:
    resource = files("osm_polygon_lucas").joinpath("lucas_2022_source.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _report(
    wkt_bytes: bytes,
    osm_crs: str,
    manifest: dict[str, Any],
    bounds: tuple[float, float, float, float],
    selection: _SourceSelection,
    coverage: PolygonCoverage,
) -> dict[str, Any]:
    return {
        "schema": "lucas_polygon_coverage.v1",
        "input": {
            "osm_crs": osm_crs,
            "osm_area": coverage.osm_area,
            "osm_wkt_sha256": hashlib.sha256(wkt_bytes).hexdigest(),
        },
        "source": {
            "asset": manifest["asset"],
            "layer": manifest["layer"],
            "crs": manifest["crs"],
            "expected_sha256": manifest["sha256"],
            "sha256_verification": "not_performed_by_cli",
        },
        "policy": {
            "minimum_class_fraction": MIN_CLASS_FRACTION,
            "comparison": ">=",
            "denominator": "full_original_osm_area",
            "missing_support": "remains_in_denominator",
            "same_class_supports": "unioned_before_measurement",
            "taxonomy_levels_aggregated_before_gate": True,
            "cross_class_overlaps": "retained_as_evidence",
            "multiple_gate_classes": "ambiguous",
        },
        "source_selection": {
            "crs": osm_crs,
            "bounds": list(bounds),
            "eligible_bbox_candidate_count": len(selection.eligible),
            "rejections": [
                {
                    "source_feature_id": rejection.source_feature_id,
                    "reasons": [reason.value for reason in rejection.reasons],
                }
                for rejection in selection.rejections
            ],
        },
        "supports": [_support_record(item) for item in coverage.supports],
        "levels": [_level_record(level) for level in coverage.levels],
    }


def _support_record(evidence: SupportEvidence) -> dict[str, Any]:
    return {
        "source_feature_id": evidence.source_feature_id,
        "point_id": evidence.point_id,
        "raw_support_class": evidence.raw_class,
        "survey_date": _date_text(evidence.survey_date),
        "target_coordinates": _coordinates_record(evidence.target_coordinates),
        "observer_gps": _coordinates_record(evidence.observer_gps),
        "core_labels": {
            "survey_lc1": evidence.core_labels.survey_lc1,
            "survey_lc2": evidence.core_labels.survey_lc2,
            "survey_lu1": evidence.core_labels.survey_lu1,
            "survey_lu2": evidence.core_labels.survey_lu2,
        },
        "intersection_area": evidence.intersection_area,
        "boundary_only": evidence.boundary_only,
    }


def _coordinates_record(coordinates: Coordinates) -> dict[str, float | None]:
    return {"lat": coordinates.lat, "long": coordinates.long}


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _level_record(level: LevelDecision) -> dict[str, Any]:
    return {
        "name": level.level,
        "status": level.status.value,
        "winner": level.winner,
        "classes": [
            {
                "label": item.label,
                "source_feature_ids": list(item.source_feature_ids),
                "covered_area": item.covered_area,
                "fraction": item.fraction,
                "meets_gate": item.meets_gate,
            }
            for item in level.classes
        ],
        "unmapped_feature_ids": list(level.unmapped_feature_ids),
        "overlaps": [
            {
                "first_feature_id": item.first_feature_id,
                "second_feature_id": item.second_feature_id,
                "area": item.area,
                "relation": item.relation.value,
            }
            for item in level.overlaps
        ],
    }


def _write_output(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")
