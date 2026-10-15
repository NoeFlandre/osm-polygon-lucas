"""Class coverage of one OSM polygon by LUCAS support polygons (Policy B)."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Final

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from osm_polygon_lucas.support_eligibility import (
    Coordinates,
    CoreLabels,
    EligibleSupport,
)

MIN_CLASS_FRACTION: Final = 0.80


class LevelStatus(StrEnum):
    WINNER = "winner"
    AMBIGUOUS = "ambiguous"
    NO_CLASS_MEETS_GATE = "no_class_meets_gate"


class OverlapRelation(StrEnum):
    SAME_CLASS = "same_class"
    CROSS_CLASS = "cross_class"
    UNMAPPED = "unmapped"


@dataclass(frozen=True, slots=True)
class TaxonomyLevel:
    """One caller-supplied hierarchy level, mapping raw support class to label."""

    name: str
    labels: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


@dataclass(frozen=True, slots=True)
class SupportEvidence:
    source_feature_id: str
    point_id: str
    raw_class: str
    survey_date: date | None
    target_coordinates: Coordinates
    observer_gps: Coordinates
    core_labels: CoreLabels
    intersection_area: float
    boundary_only: bool


@dataclass(frozen=True, slots=True)
class Overlap:
    first_feature_id: str
    second_feature_id: str
    area: float
    relation: OverlapRelation


@dataclass(frozen=True, slots=True)
class ClassCoverage:
    label: str
    source_feature_ids: tuple[str, ...]
    covered_area: float
    fraction: float
    meets_gate: bool


@dataclass(frozen=True, slots=True)
class LevelDecision:
    level: str
    status: LevelStatus
    winner: str | None
    classes: tuple[ClassCoverage, ...]
    unmapped_feature_ids: tuple[str, ...]
    overlaps: tuple[Overlap, ...]


@dataclass(frozen=True, slots=True)
class PolygonCoverage:
    osm_area: float
    supports: tuple[SupportEvidence, ...]
    levels: tuple[LevelDecision, ...]


def assess_osm_polygon(
    osm_polygon: BaseGeometry,
    supports: Sequence[EligibleSupport],
    levels: Sequence[TaxonomyLevel],
) -> PolygonCoverage:
    """Measure each supplied level's class coverage of one OSM polygon.

    The polygon is measured as given, in the supports' CRS. Its full area is the
    denominator, so uncovered area counts against every class. Supports that do
    not touch the polygon are ignored.
    """
    osm = validate_osm_polygon(osm_polygon)
    osm_area = osm.area
    touching = [support for support in supports if support.geometry.intersects(osm)]
    pieces = [support.geometry.intersection(osm) for support in touching]
    evidence = _support_evidence(touching, pieces)
    overlapping = _overlapping_pairs(pieces)
    decisions = tuple(
        _decide_level(level, touching, osm, osm_area, overlapping) for level in levels
    )
    return PolygonCoverage(osm_area=osm_area, supports=evidence, levels=decisions)


def _support_evidence(
    touching: Sequence[EligibleSupport],
    pieces: Sequence[BaseGeometry],
) -> tuple[SupportEvidence, ...]:
    return tuple(
        SupportEvidence(
            source_feature_id=support.source_feature_id,
            point_id=support.point_id,
            raw_class=support.support_class,
            survey_date=support.survey_date,
            target_coordinates=support.target_coordinates,
            observer_gps=support.observer_gps,
            core_labels=support.core_labels,
            intersection_area=piece.area,
            boundary_only=piece.area == 0.0,
        )
        for support, piece in zip(touching, pieces, strict=True)
    )


def validate_osm_polygon(geometry: BaseGeometry) -> Polygon | MultiPolygon:
    """Validate and return a positive-area polygon before spatial queries."""
    if not isinstance(geometry, Polygon | MultiPolygon):
        raise ValueError("OSM geometry must be a Polygon or MultiPolygon")
    if not geometry.is_valid:
        raise ValueError("OSM polygon must be valid")
    area = geometry.area
    if not math.isfinite(area):
        raise ValueError("OSM polygon area must be finite")
    if area <= 0.0:
        raise ValueError("OSM polygon must have positive area")
    return geometry


def _overlapping_pairs(
    pieces: Sequence[BaseGeometry],
) -> tuple[tuple[int, int, float], ...]:
    pairs: list[tuple[int, int, float]] = []
    for first, first_piece in enumerate(pieces):
        for second in range(first + 1, len(pieces)):
            area = first_piece.intersection(pieces[second]).area
            if area > 0.0:
                pairs.append((first, second, area))
    return tuple(pairs)


def _decide_level(
    level: TaxonomyLevel,
    touching: Sequence[EligibleSupport],
    osm: BaseGeometry,
    osm_area: float,
    overlapping: Sequence[tuple[int, int, float]],
) -> LevelDecision:
    labels = [level.labels.get(support.support_class) for support in touching]
    members, unmapped = _group_by_label(touching, labels)

    classes = tuple(
        _class_coverage(label, group, osm, osm_area) for label, group in members.items()
    )
    status, winner = _level_outcome(classes)

    overlaps = tuple(
        Overlap(
            first_feature_id=touching[first].source_feature_id,
            second_feature_id=touching[second].source_feature_id,
            area=area,
            relation=_relation(labels[first], labels[second]),
        )
        for first, second, area in overlapping
    )
    return LevelDecision(
        level=level.name,
        status=status,
        winner=winner,
        classes=classes,
        unmapped_feature_ids=tuple(unmapped),
        overlaps=overlaps,
    )


def _group_by_label(
    touching: Sequence[EligibleSupport],
    labels: Sequence[str | None],
) -> tuple[dict[str, list[EligibleSupport]], list[str]]:
    members: dict[str, list[EligibleSupport]] = {}
    unmapped: list[str] = []
    for support, label in zip(touching, labels, strict=True):
        if label is None:
            unmapped.append(support.source_feature_id)
        else:
            members.setdefault(label, []).append(support)
    return members, unmapped


def _level_outcome(classes: Sequence[ClassCoverage]) -> tuple[LevelStatus, str | None]:
    passing = tuple(coverage.label for coverage in classes if coverage.meets_gate)
    if len(passing) == 1:
        return LevelStatus.WINNER, passing[0]
    if passing:
        return LevelStatus.AMBIGUOUS, None
    return LevelStatus.NO_CLASS_MEETS_GATE, None


def _class_coverage(
    label: str,
    group: Sequence[EligibleSupport],
    osm: BaseGeometry,
    osm_area: float,
) -> ClassCoverage:
    union = shapely.union_all([support.geometry for support in group])
    covered_area = union.intersection(osm).area
    fraction = covered_area / osm_area
    return ClassCoverage(
        label=label,
        source_feature_ids=tuple(support.source_feature_id for support in group),
        covered_area=covered_area,
        fraction=fraction,
        meets_gate=fraction >= MIN_CLASS_FRACTION,
    )


def _relation(first: str | None, second: str | None) -> OverlapRelation:
    if first is None or second is None:
        return OverlapRelation.UNMAPPED
    if first == second:
        return OverlapRelation.SAME_CLASS
    return OverlapRelation.CROSS_CLASS
