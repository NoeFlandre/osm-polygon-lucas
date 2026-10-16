"""LUCAS 2022 support coverage for OSM polygons."""

from osm_polygon_lucas.lucas_gpkg import iter_lucas_2022_supports
from osm_polygon_lucas.lucas_taxonomy import LUCAS_C3_LEVELS
from osm_polygon_lucas.polygon_coverage import (
    ClassCoverage,
    LevelDecision,
    LevelStatus,
    Overlap,
    OverlapRelation,
    PolygonCoverage,
    SupportEvidence,
    TaxonomyLevel,
    assess_osm_polygon,
    validate_osm_polygon,
)
from osm_polygon_lucas.support_eligibility import (
    Coordinates,
    CoreLabels,
    EligibleSupport,
    Rejection,
    RejectionReason,
    classify_support_row,
)

__all__ = [
    "ClassCoverage",
    "Coordinates",
    "CoreLabels",
    "EligibleSupport",
    "LUCAS_C3_LEVELS",
    "LevelDecision",
    "LevelStatus",
    "Overlap",
    "OverlapRelation",
    "PolygonCoverage",
    "Rejection",
    "RejectionReason",
    "SupportEvidence",
    "TaxonomyLevel",
    "assess_osm_polygon",
    "classify_support_row",
    "iter_lucas_2022_supports",
    "validate_osm_polygon",
]
