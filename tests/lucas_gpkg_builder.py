"""Writes synthetic LUCAS-shaped GeoPackages for tests, never the downloaded source."""

import sqlite3
import struct
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import shapely
from shapely.geometry.base import BaseGeometry

LAYER: Final = "l2022_cop_radpoly_radqs_allatr_wcol"
RTREE: Final = f"rtree_{LAYER}_geom"
SRS_ID: Final = 3035
_ENVELOPE_BYTES: Final = {1: 32, 2: 48, 3: 48, 4: 64}

_DECLARED_TYPES: Final[Mapping[str, str]] = {
    "survey_year": "MEDIUMINT",
    "point_copernicus": "MEDIUMINT",
    "surveycprncando": "MEDIUMINT",
    "survey_obs_type": "TEXT(50)",
    "surveycprnlc": "TEXT(50)",
    "survey_date": "TEXT(50)",
    "point_id": "MEDIUMINT",
    "point_lat": "REAL",
    "point_long": "REAL",
    "survey_gps_lat": "TEXT(50)",
    "survey_gps_long": "TEXT(50)",
    "survey_lc1": "TEXT(50)",
    "survey_lc2": "TEXT(50)",
    "survey_lu1": "TEXT(64)",
    "survey_lu2": "TEXT(70)",
}

FIELD_VALUES: Final[Mapping[str, object]] = {
    "survey_year": 2022,
    "point_copernicus": 1,
    "surveycprncando": 1,
    "survey_obs_type": "Field Survey, point visible <= 100 m",
    "surveycprnlc": "C10",
    "survey_date": "2022-09-04 00:00:00.000",
    "point_id": 900001,
    "point_lat": 45.5,
    "point_long": 12.5,
    "survey_gps_lat": "45.25",
    "survey_gps_long": "12.75",
    "survey_lc1": "B11",
    "survey_lc2": None,
    "survey_lu1": "U111",
    "survey_lu2": None,
}

_METADATA_SQL: Final = """
CREATE TABLE gpkg_spatial_ref_sys (
    srs_name TEXT NOT NULL,
    srs_id INTEGER PRIMARY KEY,
    organization TEXT NOT NULL,
    organization_coordsys_id INTEGER NOT NULL,
    definition TEXT NOT NULL,
    description TEXT
);
CREATE TABLE gpkg_contents (
    table_name TEXT NOT NULL PRIMARY KEY,
    data_type TEXT NOT NULL,
    identifier TEXT UNIQUE,
    description TEXT DEFAULT '',
    last_change DATETIME NOT NULL,
    min_x DOUBLE,
    min_y DOUBLE,
    max_x DOUBLE,
    max_y DOUBLE,
    srs_id INTEGER
);
CREATE TABLE gpkg_geometry_columns (
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    geometry_type_name TEXT NOT NULL,
    srs_id INTEGER NOT NULL,
    z TINYINT NOT NULL,
    m TINYINT NOT NULL,
    PRIMARY KEY (table_name, column_name)
);
"""


@dataclass(frozen=True, slots=True)
class Feature:
    fid: int
    geometry: Any
    fields: Mapping[str, object]


def feature(fid: int, geometry: Any, **fields: object) -> Feature:
    return Feature(fid=fid, geometry=geometry, fields={**FIELD_VALUES, **fields})


def gpkg_blob(
    geometry: BaseGeometry,
    *,
    little_endian: bool = True,
    envelope_code: int = 1,
    srs_id: int = SRS_ID,
    magic: bytes = b"GP",
    version: int = 0,
    flags: int | None = None,
    output_dimension: int = 2,
) -> bytes:
    """Header, then the envelope for envelope_code 1 to 4 (XY plus zeros), then WKB."""
    order = "<" if little_endian else ">"
    default_flags = (1 if little_endian else 0) | (envelope_code << 1)
    header = magic + bytes([version, default_flags if flags is None else flags])
    header += struct.pack(f"{order}i", srs_id)
    envelope = b""
    if envelope_code:
        minx, miny, maxx, maxy = geometry.bounds
        envelope = struct.pack(f"{order}4d", minx, maxx, miny, maxy)
        envelope += bytes(_ENVELOPE_BYTES[envelope_code] - 32)
    wkb = shapely.to_wkb(
        geometry,
        byte_order=1 if little_endian else 0,
        output_dimension=output_dimension,
    )
    return header + envelope + wkb


def _blob(geometry: Any) -> Any:
    if isinstance(geometry, BaseGeometry):
        return gpkg_blob(geometry)
    return geometry


def write_lucas_gpkg(
    path: Path,
    features: Iterable[Feature],
    *,
    omit_columns: Iterable[str] = (),
    renamed_columns: Mapping[str, str] | None = None,
    contents_srs: int = SRS_ID,
    geometry_type: str = "MULTISURFACE",
    with_rtree: bool = True,
    crs_authority: str = "EPSG",
    crs_authority_code: int = SRS_ID,
    data_type: str = "features",
    geometry_srs: int = SRS_ID,
    z: int = 0,
    m: int = 0,
) -> Path:
    """Defaults match what the reader accepts. Keyword options deviate on purpose."""
    omitted = set(omit_columns)
    renamed = dict(renamed_columns or {})
    columns = [name for name in _DECLARED_TYPES if name not in omitted]
    names = [renamed.get(name, name) for name in columns]
    column_defs = ", ".join(
        f'"{name}" {_DECLARED_TYPES[column]}'
        for name, column in zip(names, columns, strict=True)
    )
    column_list = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in range(len(names) + 2))
    insert_sql = (
        f'INSERT INTO "{LAYER}" (fid, geom, {column_list}) VALUES ({placeholders})'
    )
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(_METADATA_SQL)
        conn.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, 'undefined', NULL)",
            (
                "ETRS89-extended / LAEA Europe",
                SRS_ID,
                crs_authority,
                crs_authority_code,
            ),
        )
        conn.execute(
            "INSERT INTO gpkg_contents"
            " (table_name, data_type, identifier, last_change, srs_id)"
            " VALUES (?, ?, ?, '2026-10-15T00:00:00.000Z', ?)",
            (LAYER, data_type, LAYER, contents_srs),
        )
        conn.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, ?, ?)",
            (LAYER, geometry_type, geometry_srs, z, m),
        )
        conn.execute(
            f'CREATE TABLE "{LAYER}" (fid INTEGER PRIMARY KEY, geom MULTISURFACE, '
            f"{column_defs}, poly_area_sqm REAL)"
        )
        if with_rtree:
            conn.execute(
                f'CREATE VIRTUAL TABLE "{RTREE}"'
                " USING rtree(id, minx, maxx, miny, maxy)"
            )
        for item in features:
            values = [item.fields[column] for column in columns]
            conn.execute(insert_sql, (item.fid, _blob(item.geometry), *values))
            if (
                with_rtree
                and isinstance(item.geometry, BaseGeometry)
                and not item.geometry.is_empty
            ):
                minx, miny, maxx, maxy = item.geometry.bounds
                conn.execute(
                    f'INSERT INTO "{RTREE}" VALUES (?, ?, ?, ?, ?)',
                    (item.fid, minx, maxx, miny, maxy),
                )
        conn.commit()
    return path
