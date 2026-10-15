"""Boundary parser for LUCAS 2022 Copernicus support rows."""

import numbers
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

SURVEY_YEAR: Final = 2022
REQUIRED_KEYS: Final = (
    "survey_year",
    "point_copernicus",
    "surveycprncando",
    "survey_obs_type",
    "surveycprnlc",
    "survey_date",
    "point_id",
    "point_lat",
    "point_long",
    "survey_gps_lat",
    "survey_gps_long",
    "survey_lc1",
    "survey_lc2",
    "survey_lu1",
    "survey_lu2",
)


class RejectionReason(StrEnum):
    SURVEY_YEAR = "survey_year"
    POINT_COPERNICUS = "point_copernicus"
    SURVEY_CPRN_CAN_DO = "surveycprncando"
    SURVEY_OBS_TYPE = "survey_obs_type"
    SUPPORT_CLASS = "surveycprnlc"
    GEOMETRY_NOT_POLYGONAL = "geometry_not_polygonal"
    GEOMETRY_INVALID = "geometry_invalid"
    GEOMETRY_NOT_POSITIVE_AREA = "geometry_not_positive_area"


@dataclass(frozen=True, slots=True)
class Coordinates:
    lat: float | None
    long: float | None


@dataclass(frozen=True, slots=True)
class CoreLabels:
    survey_lc1: str | None
    survey_lc2: str | None
    survey_lu1: str | None
    survey_lu2: str | None


@dataclass(frozen=True, slots=True)
class EligibleSupport:
    source_feature_id: str
    point_id: str
    support_class: str
    survey_date: date | None
    target_coordinates: Coordinates
    observer_gps: Coordinates
    core_labels: CoreLabels
    geometry: Polygon | MultiPolygon


@dataclass(frozen=True, slots=True)
class Rejection:
    source_feature_id: str
    reasons: tuple[RejectionReason, ...]


_CODE_CRITERIA: Final = (
    (RejectionReason.SURVEY_YEAR, "survey_year", SURVEY_YEAR),
    (RejectionReason.POINT_COPERNICUS, "point_copernicus", 1),
    (RejectionReason.SURVEY_CPRN_CAN_DO, "surveycprncando", 1),
    (RejectionReason.SURVEY_OBS_TYPE, "survey_obs_type", 1),
)


def classify_support_row(
    source_feature_id: str,
    row: Mapping[str, object],
    geometry: BaseGeometry | None,
) -> EligibleSupport | Rejection:
    """Return an eligible support, or every failed criterion for the row.

    Missing keys and wrongly typed carried fields raise on every row, so schema
    errors never hide inside a rejection. The eligibility codes in
    `_CODE_CRITERIA` are criteria, not carried fields. A code that does not
    match, or has the wrong type, returns a Rejection. A `None` surveycprnlc
    returns a Rejection with the SUPPORT_CLASS reason. A numeric or otherwise
    non-string surveycprnlc raises TypeError.
    """
    _require_keys(row)

    point_id = _point_id(row["point_id"])
    survey_date = _optional_date(row["survey_date"])
    target = Coordinates(
        lat=_optional_float(row["point_lat"]),
        long=_optional_float(row["point_long"]),
    )
    observer = Coordinates(
        lat=_optional_float(row["survey_gps_lat"]),
        long=_optional_float(row["survey_gps_long"]),
    )
    core = CoreLabels(
        survey_lc1=_optional_str(row["survey_lc1"]),
        survey_lc2=_optional_str(row["survey_lc2"]),
        survey_lu1=_optional_str(row["survey_lu1"]),
        survey_lu2=_optional_str(row["survey_lu2"]),
    )

    support_class = _optional_str(row["surveycprnlc"])
    polygon = geometry if isinstance(geometry, Polygon | MultiPolygon) else None
    reasons = _rejection_reasons(row, support_class, polygon)
    if reasons or polygon is None or not isinstance(support_class, str):
        return Rejection(source_feature_id=source_feature_id, reasons=reasons)
    return EligibleSupport(
        source_feature_id=source_feature_id,
        point_id=point_id,
        support_class=support_class,
        survey_date=survey_date,
        target_coordinates=target,
        observer_gps=observer,
        core_labels=core,
        geometry=polygon,
    )


def _require_keys(row: Mapping[str, object]) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in row]
    if missing:
        raise ValueError(f"JRC row is missing required lower-case keys: {missing}")


def _rejection_reasons(
    row: Mapping[str, object],
    support_class: object,
    polygon: Polygon | MultiPolygon | None,
) -> tuple[RejectionReason, ...]:
    reasons: list[RejectionReason] = [
        reason
        for reason, key, expected in _CODE_CRITERIA
        if not _is_code(row[key], expected)
    ]
    if not isinstance(support_class, str):
        reasons.append(RejectionReason.SUPPORT_CLASS)
    reasons.extend(_geometry_reasons(polygon))
    return tuple(reasons)


def _geometry_reasons(
    polygon: Polygon | MultiPolygon | None,
) -> tuple[RejectionReason, ...]:
    if polygon is None:
        return (RejectionReason.GEOMETRY_NOT_POLYGONAL,)
    if not polygon.is_valid:
        return (RejectionReason.GEOMETRY_INVALID,)
    if polygon.area <= 0.0:
        return (RejectionReason.GEOMETRY_NOT_POSITIVE_AREA,)
    return ()


def _is_code(value: object, expected: int) -> bool:
    return (
        isinstance(value, numbers.Integral)
        and not isinstance(value, bool)
        and int(value) == expected
    )


def _point_id(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return str(int(value))
    raise TypeError(
        f"point_id must be a string or an integer, not {type(value).__name__}"
    )


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise TypeError(f"survey_date must be a date or None, not {type(value).__name__}")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"coordinate must be a number or None, not {type(value).__name__}")


def _optional_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"label must be a string or None, not {type(value).__name__}")
