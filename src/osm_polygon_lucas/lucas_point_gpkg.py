"""Read only actual-GPS Copernicus observations from the LUCAS GeoPackage."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Final

from osm_polygon_lucas.lucas_gpkg import (
    LAYER,
    _check_crs,
    _check_layer,
    _open_read_only,
)
from osm_polygon_lucas.point_coverage import PointObservation

_POINT_FIELDS: Final = (
    "point_id",
    "survey_year",
    "point_copernicus",
    "surveycprncando",
    "survey_obs_type",
    "surveycprnlc",
    "survey_gps_lat",
    "survey_gps_long",
)
_OBSERVATION_TYPES: Final = {
    "Field Survey, point visible <= 100 m": 1,
    "Field Survey, point visible > 100 m": 2,
    "Photo-interpretation in the field": 3,
}


def iter_lucas_2022_point_observations(
    path: str | Path,
) -> Iterator[PointObservation]:
    """Yield raw survey GPS and Copernicus labels without reading polygon WKB.

    Unknown observation-mode text is retained as-is so the point census can
    count it as a quality exclusion instead of failing the whole layer scan.
    """
    with closing(_open_read_only(Path(path))) as conn:
        try:
            _check_layer(conn)
            _check_crs(conn)
            _check_point_fields(conn)
            columns = ", ".join(("fid", *_POINT_FIELDS))
            cursor = conn.execute(f'SELECT {columns} FROM "{LAYER}" ORDER BY fid')
            for fid, *values in cursor:
                row = dict(zip(_POINT_FIELDS, values, strict=True))
                yield PointObservation(
                    source_feature_id=str(fid),
                    point_id=row["point_id"],
                    survey_year=row["survey_year"],
                    point_copernicus=row["point_copernicus"],
                    surveycprncando=row["surveycprncando"],
                    survey_obs_type=_observation_type(row["survey_obs_type"]),
                    surveycprnlc=row["surveycprnlc"],
                    survey_gps_lat=row["survey_gps_lat"],
                    survey_gps_long=row["survey_gps_long"],
                )
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                f"{path} is not a readable LUCAS GeoPackage: {exc}"
            ) from exc


def _check_point_fields(conn: sqlite3.Connection) -> None:
    names = {row[1] for row in conn.execute(f'PRAGMA table_info("{LAYER}")')}
    missing = [name for name in _POINT_FIELDS if name not in names]
    if missing:
        raise ValueError(f"{LAYER} lacks point-census columns {missing}")


def _observation_type(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _OBSERVATION_TYPES.get(value, value)
