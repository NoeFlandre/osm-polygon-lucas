"""Point-based LUCAS validation, separate from polygon-area coverage."""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, TypeGuard, cast

import shapely
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from osm_polygon_lucas.lucas_taxonomy import LUCAS_C3_LEVELS
from osm_polygon_lucas.polygon_coverage import TaxonomyLevel, validate_osm_polygon
from osm_polygon_lucas.support_eligibility import SURVEY_YEAR

_OBSERVATION_TYPES: Final[Mapping[str, int]] = {
    "Field Survey, point visible <= 100 m": 1,
    "Field Survey, point visible > 100 m": 2,
    "Photo-interpretation in the field": 3,
}
_OBSERVATION_MODE_NAMES: Final[Mapping[int, str]] = {
    1: "field_visible_within_100m",
    2: "field_visible_over_100m",
    3: "field_photo_interpretation",
}
_LEVEL_BY_NAME: Final[Mapping[str, TaxonomyLevel]] = {
    level.name: level for level in LUCAS_C3_LEVELS
}


class PointPolygonStatus(StrEnum):
    """Result for one unique source polygon with at least one located observation."""

    ELIGIBLE = "eligible"
    CONFLICTING_LABELS = "conflicting_labels"
    NO_VALID_LABEL = "no_valid_label"


@dataclass(frozen=True, slots=True)
class PointObservation:
    """Raw LUCAS point evidence; coordinates are the surveyor's GPS fields."""

    source_feature_id: str
    point_id: object
    survey_year: object
    point_copernicus: object
    surveycprncando: object
    survey_obs_type: object
    surveycprnlc: object
    survey_gps_lat: object
    survey_gps_long: object


@dataclass(frozen=True, slots=True)
class SourcePolygon:
    """One source polygon in OGC:CRS84 longitude/latitude coordinates."""

    polygon_id: str
    geometry: BaseGeometry


@dataclass(frozen=True, slots=True)
class PointPolygonDecision:
    polygon_id: str
    status: PointPolygonStatus
    label: str | None
    located_observations: int
    valid_observations: int
    missing_label_observations: int
    invalid_label_observations: int
    unmapped_label_observations: int
    observation_mode_counts: Mapping[str, int]
    observed_labels: tuple[str, ...]
    anchor_longitude: float
    anchor_latitude: float


@dataclass(frozen=True, slots=True)
class PointCensus:
    taxonomy_level: str
    taxonomy_mapping_sha256: str
    source_polygon_count: int
    observation_count: int
    duplicate_records_skipped: int
    quality_valid_observations: int
    located_observations: int
    valid_labeled_observations: int
    eligible_polygon_count: int
    conflict_polygon_count: int
    no_valid_label_polygon_count: int
    boundary_observations: int
    outside_observations: int
    multiple_match_observations: int
    rejection_counts: Mapping[str, int]
    input_observation_modes: Mapping[str, int]
    quality_valid_observation_modes: Mapping[str, int]
    located_observation_modes: Mapping[str, int]
    decisions: tuple[PointPolygonDecision, ...]

    @property
    def eligible_polygon_labels(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                decision.polygon_id: decision.label
                for decision in self.decisions
                if decision.status is PointPolygonStatus.ELIGIBLE
                and decision.label is not None
            }
        )

    @property
    def eligible_polygon_anchors(self) -> Mapping[str, tuple[float, float]]:
        return MappingProxyType(
            {
                decision.polygon_id: (
                    decision.anchor_longitude,
                    decision.anchor_latitude,
                )
                for decision in self.decisions
                if decision.status is PointPolygonStatus.ELIGIBLE
            }
        )


def evaluate_point_census(
    polygons: Sequence[SourcePolygon],
    observations: Iterable[PointObservation],
    *,
    taxonomy_level: str = "main_category",
) -> PointCensus:
    """Assign quality-valid GPS observations by exact containment in CRS84.

    The observed Copernicus class is ``surveycprnlc`` and the point is built
    from ``survey_gps_long``/``survey_gps_lat``. The theoretical target and the
    core ``survey_lc1`` field are deliberately absent from this path. All three
    recognized observation modes are retained and reported separately.
    """
    level = _taxonomy_level(taxonomy_level)
    index = _prepare_polygons(polygons)
    accumulator = _new_accumulator()
    for observation in observations:
        accumulator.observation_count += 1
        _record_observation(accumulator, index, observation, level)
    return _build_census(accumulator, index, level, len(polygons))


@dataclass(frozen=True, slots=True)
class _PolygonMatches:
    polygon_index: int | None
    reason: str | None
    multiple_matches: bool = False


@dataclass(frozen=True, slots=True)
class _PolygonIndex:
    ids: tuple[str, ...]
    geometries: tuple[BaseGeometry, ...]
    anchors: Mapping[str, tuple[float, float]]
    tree: STRtree | None


@dataclass(slots=True)
class _CensusAccumulator:
    seen: dict[str, PointObservation]
    groups: dict[str, _PolygonGroup]
    rejection_counts: Counter[str]
    input_modes: Counter[str]
    quality_modes: Counter[str]
    located_modes: Counter[str]
    observation_count: int = 0
    duplicate_records: int = 0
    quality_valid_count: int = 0
    located_count: int = 0
    valid_labeled_count: int = 0
    boundary_count: int = 0
    outside_count: int = 0
    multiple_count: int = 0


@dataclass(slots=True)
class _PolygonGroup:
    located: int = 0
    valid: int = 0
    missing_label: int = 0
    invalid_label: int = 0
    unmapped_label: int = 0
    labels: set[str] = field(default_factory=set)
    modes: Counter[str] = field(default_factory=Counter)


def _taxonomy_level(name: str) -> TaxonomyLevel:
    try:
        return _LEVEL_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown LUCAS C3 taxonomy level: {name}") from exc


def _taxonomy_digest(level: TaxonomyLevel) -> str:
    payload = json.dumps(
        {"name": level.name, "labels": dict(sorted(level.labels.items()))},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prepare_polygons(
    polygons: Sequence[SourcePolygon],
) -> _PolygonIndex:
    ids: list[str] = []
    geometries: list[BaseGeometry] = []
    anchors: dict[str, tuple[float, float]] = {}
    seen: set[str] = set()
    for source_polygon in polygons:
        _validate_polygon_id(source_polygon.polygon_id, seen)
        seen.add(source_polygon.polygon_id)
        geometry = validate_osm_polygon(source_polygon.geometry)
        _validate_crs84_geometry(source_polygon.polygon_id, geometry)
        representative = geometry.representative_point()
        ids.append(source_polygon.polygon_id)
        geometries.append(geometry)
        anchors[source_polygon.polygon_id] = (representative.x, representative.y)
    return _PolygonIndex(
        ids=tuple(ids),
        geometries=tuple(geometries),
        anchors=MappingProxyType(anchors),
        tree=shapely.STRtree(geometries) if geometries else None,
    )


def _validate_polygon_id(polygon_id: str, seen: set[str]) -> None:
    if not isinstance(polygon_id, str) or not polygon_id or polygon_id in seen:
        raise ValueError("source polygon IDs must be non-empty and unique")


def _validate_crs84_geometry(polygon_id: str, geometry: BaseGeometry) -> None:
    bounds = geometry.bounds
    if not _finite_bounds(bounds):
        raise ValueError(f"{polygon_id}: geometry has non-finite OGC:CRS84 bounds")
    minx, miny, maxx, maxy = bounds
    if not _bounds_within_crs84(minx, miny, maxx, maxy):
        raise ValueError(f"{polygon_id}: geometry is outside OGC:CRS84 bounds")


def _finite_bounds(bounds: tuple[float, ...]) -> bool:
    return all(map(math.isfinite, bounds))


def _bounds_within_crs84(minx: float, miny: float, maxx: float, maxy: float) -> bool:
    return minx >= -180 and maxx <= 180 and miny >= -90 and maxy <= 90


def _new_accumulator() -> _CensusAccumulator:
    return _CensusAccumulator(
        seen={},
        groups={},
        rejection_counts=Counter(),
        input_modes=Counter(),
        quality_modes=Counter(),
        located_modes=Counter(),
    )


def _record_observation(
    accumulator: _CensusAccumulator,
    index: _PolygonIndex,
    observation: PointObservation,
    level: TaxonomyLevel,
) -> None:
    if not _remember_observation(accumulator, observation):
        return
    mode = _observation_mode(observation.survey_obs_type)
    accumulator.input_modes[mode or "unknown_or_missing"] += 1
    reasons = _quality_reasons(observation, mode)
    if reasons:
        accumulator.rejection_counts.update(reasons)
        return
    _record_quality_valid_observation(accumulator, index, observation, mode, level)


def _record_quality_valid_observation(
    accumulator: _CensusAccumulator,
    index: _PolygonIndex,
    observation: PointObservation,
    mode: str | None,
    level: TaxonomyLevel,
) -> None:
    coordinates, coordinate_reason = _gps_coordinates(observation)
    if coordinate_reason is not None:
        accumulator.rejection_counts[coordinate_reason] += 1
        return
    coordinates = cast(tuple[float, float], coordinates)
    mode = cast(str, mode)
    accumulator.quality_valid_count += 1
    accumulator.quality_modes[mode] += 1
    _record_spatial_observation(
        accumulator, index, observation, coordinates, mode, level
    )


def _remember_observation(
    accumulator: _CensusAccumulator, observation: PointObservation
) -> bool:
    _validate_observation_id(observation)
    previous = accumulator.seen.get(observation.source_feature_id)
    if previous is None:
        accumulator.seen[observation.source_feature_id] = observation
        return True
    if previous != observation:
        raise ValueError("duplicate source feature ID has conflicting observation data")
    accumulator.duplicate_records += 1
    return False


def _record_spatial_observation(
    accumulator: _CensusAccumulator,
    index: _PolygonIndex,
    observation: PointObservation,
    coordinates: tuple[float, float],
    mode: str,
    level: TaxonomyLevel,
) -> None:
    point = Point(*coordinates)
    matches = _point_polygon_matches(point, index.geometries, index.tree)
    if matches.reason is not None:
        accumulator.rejection_counts[matches.reason] += 1
        _record_spatial_exclusion_counters(
            accumulator, matches.reason, matches.multiple_matches
        )
        return
    assert matches.polygon_index is not None
    polygon_id = index.ids[matches.polygon_index]
    _record_polygon_match(accumulator, polygon_id, observation, mode, level)


def _record_spatial_exclusion_counters(
    accumulator: _CensusAccumulator, reason: str, multiple_matches: bool
) -> None:
    if reason == "boundary_point":
        accumulator.boundary_count += 1
    if reason == "outside_source_polygons":
        accumulator.outside_count += 1
    if multiple_matches:
        _record_multiple_match(accumulator, reason)


def _record_multiple_match(accumulator: _CensusAccumulator, reason: str) -> None:
    accumulator.multiple_count += 1
    if reason != "multiple_source_polygons":
        accumulator.rejection_counts["multiple_source_polygons"] += 1


def _record_polygon_match(
    accumulator: _CensusAccumulator,
    polygon_id: str,
    observation: PointObservation,
    mode: str,
    level: TaxonomyLevel,
) -> None:
    group = accumulator.groups.setdefault(polygon_id, _PolygonGroup())
    group.located += 1
    group.modes[mode] += 1
    accumulator.located_modes[mode] += 1
    accumulator.located_count += 1
    label, reason = _mapped_label(observation.surveycprnlc, level)
    if reason is not None:
        _record_label_issue(accumulator, group, reason)
        return
    assert label is not None
    group.labels.add(label)
    group.valid += 1
    accumulator.valid_labeled_count += 1


def _record_label_issue(
    accumulator: _CensusAccumulator, group: _PolygonGroup, reason: str
) -> None:
    accumulator.rejection_counts[reason] += 1
    if reason == "missing_label":
        group.missing_label += 1
    elif reason == "invalid_label":
        group.invalid_label += 1
    else:
        group.unmapped_label += 1


def _validate_observation_id(observation: PointObservation) -> None:
    if (
        not isinstance(observation.source_feature_id, str)
        or not observation.source_feature_id
    ):
        raise ValueError("observation source feature IDs must be non-empty text")


def _observation_mode(value: object) -> str | None:
    code = value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(value, str):
        code = _OBSERVATION_TYPES.get(value)
    return _OBSERVATION_MODE_NAMES.get(code) if code is not None else None


def _quality_reasons(
    observation: PointObservation, mode: str | None
) -> tuple[str, ...]:
    reasons: list[str] = []
    reasons.extend(_expected_flag(observation.survey_year, SURVEY_YEAR, "survey_year"))
    reasons.extend(_expected_flag(observation.point_copernicus, 1, "point_copernicus"))
    reasons.extend(_expected_flag(observation.surveycprncando, 1, "surveycprncando"))
    if observation.survey_obs_type is None:
        reasons.append("missing_survey_obs_type")
    elif mode is None:
        reasons.append("unknown_survey_obs_type")
    return tuple(reasons)


def _expected_flag(value: object, expected: int, name: str) -> tuple[str, ...]:
    if value is None:
        return (f"missing_{name}",)
    if not _is_integer_flag(value):
        return (f"invalid_{name}",)
    return _expected_flag_result(int(value), expected, name)


def _is_integer_flag(value: object) -> TypeGuard[numbers.Integral]:
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _expected_flag_result(value: int, expected: int, name: str) -> tuple[str, ...]:
    if value == expected:
        return ()
    if name == "survey_year":
        return ("wrong_survey_year",)
    return (f"not_{name}",)


def _gps_coordinates(
    observation: PointObservation,
) -> tuple[tuple[float, float] | None, str | None]:
    if observation.survey_gps_lat is None or observation.survey_gps_long is None:
        return None, "missing_gps_coordinates"
    latitude = _float_coordinate(observation.survey_gps_lat)
    longitude = _float_coordinate(observation.survey_gps_long)
    if latitude is None or longitude is None:
        return None, "invalid_gps_coordinates"
    return _checked_gps_pair(longitude, latitude)


def _checked_gps_pair(
    longitude: float, latitude: float
) -> tuple[tuple[float, float] | None, str | None]:
    if not _valid_latitude(latitude):
        return None, "invalid_gps_coordinates"
    if not _valid_longitude(longitude):
        return None, "invalid_gps_coordinates"
    return (longitude, latitude), None


def _float_coordinate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_latitude(value: float) -> bool:
    return math.isfinite(value) and -90 <= value <= 90


def _valid_longitude(value: float) -> bool:
    return math.isfinite(value) and -180 <= value <= 180


def _point_polygon_matches(
    point: Point,
    geometries: Sequence[BaseGeometry],
    tree: STRtree | None,
) -> _PolygonMatches:
    if tree is None:
        return _PolygonMatches(None, "outside_source_polygons")
    matched = _covering_polygon_indices(point, geometries, tree)
    return _match_outcome(point, matched, geometries)


def _covering_polygon_indices(
    point: Point, geometries: Sequence[BaseGeometry], tree: STRtree
) -> list[int]:
    matched = [
        int(index)
        for index in tree.query(point)
        if geometries[int(index)].covers(point)
    ]
    return matched


def _match_outcome(
    point: Point, matched: Sequence[int], geometries: Sequence[BaseGeometry]
) -> _PolygonMatches:
    if not matched:
        return _PolygonMatches(None, "outside_source_polygons")
    if _is_boundary_match(point, matched, geometries):
        return _PolygonMatches(None, "boundary_point", len(matched) > 1)
    if len(matched) > 1:
        return _PolygonMatches(None, "multiple_source_polygons", multiple_matches=True)
    return _PolygonMatches(matched[0], None)


def _is_boundary_match(
    point: Point, matched: Sequence[int], geometries: Sequence[BaseGeometry]
) -> bool:
    return any(not geometries[index].contains(point) for index in matched)


def _mapped_label(value: object, level: TaxonomyLevel) -> tuple[str | None, str | None]:
    if value is None or value == "":
        return None, "missing_label"
    if not isinstance(value, str):
        return None, "invalid_label"
    label = level.labels.get(value)
    if label is None:
        return None, "unmapped_label"
    return label, None


def _decisions(
    groups: Mapping[str, _PolygonGroup],
    anchors: Mapping[str, tuple[float, float]],
) -> tuple[PointPolygonDecision, ...]:
    results: list[PointPolygonDecision] = []
    for polygon_id, group in sorted(groups.items()):
        observed_labels = tuple(sorted(group.labels))
        if not observed_labels:
            status, label = PointPolygonStatus.NO_VALID_LABEL, None
        elif len(observed_labels) > 1:
            status, label = PointPolygonStatus.CONFLICTING_LABELS, None
        else:
            status, label = PointPolygonStatus.ELIGIBLE, observed_labels[0]
        anchor = anchors.get(polygon_id)
        if anchor is None:
            raise ValueError("point census is missing a source polygon anchor")
        results.append(
            PointPolygonDecision(
                polygon_id=polygon_id,
                status=status,
                label=label,
                located_observations=group.located,
                valid_observations=group.valid,
                missing_label_observations=group.missing_label,
                invalid_label_observations=group.invalid_label,
                unmapped_label_observations=group.unmapped_label,
                observation_mode_counts=MappingProxyType(
                    dict(sorted(group.modes.items()))
                ),
                observed_labels=observed_labels,
                anchor_longitude=anchor[0],
                anchor_latitude=anchor[1],
            )
        )
    return tuple(results)


def _build_census(
    accumulator: _CensusAccumulator,
    index: _PolygonIndex,
    level: TaxonomyLevel,
    source_polygon_count: int,
) -> PointCensus:
    decisions = _decisions(accumulator.groups, index.anchors)
    status_counts = Counter(decision.status for decision in decisions)
    return PointCensus(
        taxonomy_level=level.name,
        taxonomy_mapping_sha256=_taxonomy_digest(level),
        source_polygon_count=source_polygon_count,
        observation_count=accumulator.observation_count,
        duplicate_records_skipped=accumulator.duplicate_records,
        quality_valid_observations=accumulator.quality_valid_count,
        located_observations=accumulator.located_count,
        valid_labeled_observations=accumulator.valid_labeled_count,
        eligible_polygon_count=status_counts[PointPolygonStatus.ELIGIBLE],
        conflict_polygon_count=status_counts[PointPolygonStatus.CONFLICTING_LABELS],
        no_valid_label_polygon_count=status_counts[PointPolygonStatus.NO_VALID_LABEL],
        boundary_observations=accumulator.boundary_count,
        outside_observations=accumulator.outside_count,
        multiple_match_observations=accumulator.multiple_count,
        rejection_counts=MappingProxyType(
            dict(sorted(accumulator.rejection_counts.items()))
        ),
        input_observation_modes=MappingProxyType(
            dict(sorted(accumulator.input_modes.items()))
        ),
        quality_valid_observation_modes=MappingProxyType(
            dict(sorted(accumulator.quality_modes.items()))
        ),
        located_observation_modes=MappingProxyType(
            dict(sorted(accumulator.located_modes.items()))
        ),
        decisions=decisions,
    )
