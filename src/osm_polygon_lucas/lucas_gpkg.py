"""Read-only loader for the LUCAS 2022 support layer of a GeoPackage.

Missing files raise FileNotFoundError. Other source and schema faults raise ValueError,
so no value is mapped silently.
"""

import math
import numbers
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, TypeAlias

import shapely
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry

from osm_polygon_lucas.support_eligibility import (
    REQUIRED_KEYS,
    EligibleSupport,
    Rejection,
    classify_support_row,
)

LAYER: Final = "l2022_cop_radpoly_radqs_allatr_wcol"
RTREE: Final = f"rtree_{LAYER}_geom"
SRS_ID: Final = 3035
_SELECT_COLUMNS: Final = ("fid", "geom", *REQUIRED_KEYS)
_ENVELOPE_BYTES: Final = (0, 32, 48, 48, 64)
_DATE_FORMAT: Final = "%Y-%m-%d %H:%M:%S.%f"
_OBSERVATION_CODES: Final[Mapping[str, int]] = {
    "Field Survey, point visible <= 100 m": 1,
    "Field Survey, point visible > 100 m": 2,
    "Photo-interpretation in the field": 3,
}
_Normalizer: TypeAlias = Callable[[int, str, object], object]


@dataclass(frozen=True, slots=True)
class _Box:
    minx: float
    miny: float
    maxx: float
    maxy: float


def iter_lucas_2022_supports(
    path: str | Path,
    *,
    bounds: tuple[float, float, float, float] | list[float] | None = None,
) -> Iterator[EligibleSupport | Rejection]:
    """Yield one result per selected feature, in fid order.

    Bounds (minx, miny, maxx, maxy) select features by their RTree boxes,
    conservatively. Exact intersection is left to assess_osm_polygon. The arguments
    and the file are checked on the first next().
    """
    box = _parse_bounds(bounds)
    with closing(_open_read_only(Path(path))) as conn:
        try:
            _check_layer(conn)
            _check_crs(conn)
            _check_fields(conn)
            if box is not None:
                _check_rtree(conn)
            for fid, blob, *values in _select_rows(conn, box):
                geometry = _decode_geometry(fid, blob)
                row = _normalize_row(fid, values)
                yield _classify(fid, row, geometry)
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                f"{path} is not a readable LUCAS GeoPackage: {exc}"
            ) from exc


def _parse_bounds(bounds: object) -> _Box | None:
    if bounds is None:
        return None
    minx, miny, maxx, maxy = _bound_values(bounds)
    if minx > maxx or miny > maxy:
        raise ValueError(f"bounds {bounds!r} need minx <= maxx and miny <= maxy")
    return _Box(minx=minx, miny=miny, maxx=maxx, maxy=maxy)


def _bound_values(bounds: object) -> tuple[float, float, float, float]:
    if not isinstance(bounds, tuple | list) or len(bounds) != 4:
        raise ValueError(f"bounds must be four finite numbers, not {bounds!r}")
    minx, miny, maxx, maxy = map(_finite_number, bounds)
    return minx, miny, maxx, maxy


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"bounds value {value!r} is not a real number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"bounds value {value!r} is too large for a float") from exc
    if not math.isfinite(number):
        raise ValueError(f"bounds value {value!r} is not finite")
    return number


def _open_read_only(path: Path) -> sqlite3.Connection:
    # sqlite3 reports a missing file as DatabaseError under mode=ro. Checking first
    # keeps the FileNotFoundError that callers expect.
    if not path.is_file():
        raise FileNotFoundError(f"no LUCAS GeoPackage file at {path}")
    try:
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"cannot open {path} read-only: {exc}") from exc


def _fetch_row(
    conn: sqlite3.Connection, sql: str, params: tuple[object, ...], item: str
) -> tuple[object, ...]:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise ValueError(f"GeoPackage has no {item}")
    return row


def _expect(item: str, found: object, expected: object) -> None:
    if found != expected:
        raise ValueError(f"{item} is {found!r}, expected {expected!r}")


def _check_layer(conn: sqlite3.Connection) -> None:
    column = f"{LAYER}.geom"
    data_type, contents_srs = _fetch_row(
        conn,
        "SELECT data_type, srs_id FROM gpkg_contents WHERE table_name = ?",
        (LAYER,),
        f"gpkg_contents row for {LAYER}",
    )
    _expect(f"gpkg_contents data_type of {LAYER}", data_type, "features")
    _expect(f"gpkg_contents srs_id of {LAYER}", contents_srs, SRS_ID)
    geometry_type, column_srs, z, m = _fetch_row(
        conn,
        "SELECT geometry_type_name, srs_id, z, m FROM gpkg_geometry_columns "
        "WHERE table_name = ? AND column_name = 'geom'",
        (LAYER,),
        f"gpkg_geometry_columns row for {column}",
    )
    _expect(f"geometry_type_name of {column}", geometry_type, "MULTISURFACE")
    _expect(f"srs_id of {column}", column_srs, SRS_ID)
    _expect(f"z of {column}", z, 0)
    _expect(f"m of {column}", m, 0)


def _check_crs(conn: sqlite3.Connection) -> None:
    organization, coordsys_id = _fetch_row(
        conn,
        "SELECT organization, organization_coordsys_id FROM gpkg_spatial_ref_sys "
        "WHERE srs_id = ?",
        (SRS_ID,),
        f"gpkg_spatial_ref_sys row for srs_id {SRS_ID}",
    )
    _expect(f"organization of srs_id {SRS_ID}", organization, "EPSG")
    _expect(f"organization_coordsys_id of srs_id {SRS_ID}", coordsys_id, SRS_ID)


def _check_fields(conn: sqlite3.Connection) -> None:
    names = {row[1] for row in conn.execute(f'PRAGMA table_info("{LAYER}")')}
    missing = [name for name in _SELECT_COLUMNS if name not in names]
    if missing:
        raise ValueError(f"{LAYER} lacks required columns {missing}")


def _check_rtree(conn: sqlite3.Connection) -> None:
    _fetch_row(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (RTREE,),
        f"RTREE table {RTREE} for bounds",
    )


def _select_rows(conn: sqlite3.Connection, box: _Box | None) -> sqlite3.Cursor:
    columns = ", ".join(f"t.{name}" for name in _SELECT_COLUMNS)
    sql = f'SELECT {columns} FROM "{LAYER}" AS t'
    params: dict[str, float] = {}
    if box is not None:
        sql += (
            f' JOIN "{RTREE}" AS r ON r.id = t.fid'
            " WHERE r.maxx >= :minx AND r.minx <= :maxx"
            " AND r.maxy >= :miny AND r.miny <= :maxy"
        )
        params = {
            "minx": box.minx,
            "maxx": box.maxx,
            "miny": box.miny,
            "maxy": box.maxy,
        }
    return conn.execute(f"{sql} ORDER BY t.fid", params)


def _decode_geometry(fid: int, blob: bytes | None) -> BaseGeometry | None:
    """Valid 2D non-polygons pass through, because the parser owns the polygon rule."""
    if blob is None:
        return None
    if not isinstance(blob, bytes):
        raise ValueError(f"fid {fid}: geometry is not a BLOB")
    return _decode_wkb(fid, blob[_wkb_start(fid, blob) :])


def _wkb_start(fid: int, blob: bytes) -> int:
    _check_signature(fid, blob)
    flags = blob[3]
    order = "little" if flags & 0x01 else "big"
    envelope = _envelope_code(fid, flags)
    srs_id = int.from_bytes(blob[4:8], order, signed=True)
    _check_srs(fid, srs_id)
    wkb_start = 8 + _ENVELOPE_BYTES[envelope]
    if len(blob) < wkb_start:
        raise ValueError(f"fid {fid}: truncated GeoPackage envelope")
    return wkb_start


def _check_signature(fid: int, blob: bytes) -> None:
    if len(blob) < 8:
        raise ValueError(f"fid {fid}: truncated GeoPackage header")
    if blob[:2] != b"GP":
        raise ValueError(f"fid {fid}: GeoPackage magic {blob[:2]!r} is not b'GP'")
    if blob[2] != 0:
        raise ValueError(f"fid {fid}: GeoPackage version {blob[2]} is not 0")


def _envelope_code(fid: int, flags: int) -> int:
    if flags & 0xE0:
        raise ValueError(f"fid {fid}: GeoPackage flags {flags:#04x} set bits 5 to 7")
    envelope = (flags >> 1) & 0x07
    if envelope > 4:
        raise ValueError(f"fid {fid}: GeoPackage envelope code {envelope} is not 0-4")
    return envelope


def _check_srs(fid: int, srs_id: int) -> None:
    if srs_id != SRS_ID:
        raise ValueError(f"fid {fid}: GeoPackage srs_id {srs_id} is not {SRS_ID}")


def _decode_wkb(fid: int, wkb: bytes) -> BaseGeometry:
    order = _wkb_order(fid, wkb)
    geometry_type = int.from_bytes(wkb[1:5], order)
    if not 1 <= geometry_type <= 7:
        raise ValueError(f"fid {fid}: WKB type {geometry_type} is not a 2D type")
    try:
        return shapely.from_wkb(wkb)
    except ShapelyError as exc:
        raise ValueError(f"fid {fid}: malformed WKB geometry") from exc


def _wkb_order(fid: int, wkb: bytes) -> Literal["little", "big"]:
    if len(wkb) < 5:
        raise ValueError(f"fid {fid}: truncated WKB header")
    if wkb[0] not in (0, 1):
        raise ValueError(f"fid {fid}: WKB byte order {wkb[0]} is not 0 or 1")
    return "little" if wkb[0] else "big"


def _normalize_row(fid: int, values: Sequence[object]) -> dict[str, object]:
    row = dict(zip(REQUIRED_KEYS, values, strict=True))
    for key, normalize in _NORMALIZERS.items():
        row[key] = normalize(fid, key, row[key])
    return row


def _classify(
    fid: int, row: Mapping[str, object], geometry: BaseGeometry | None
) -> EligibleSupport | Rejection:
    """A TypeError from the parser means a schema fault, so raise ValueError."""
    try:
        return classify_support_row(str(fid), row, geometry)
    except TypeError as exc:
        raise ValueError(f"fid {fid}: {exc}") from exc


def _text_or_none(fid: int, key: str, value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"fid {fid}: {key} must be text, not {type(value).__name__}")


def _observation_code(fid: int, key: str, value: object) -> object:
    text = _text_or_none(fid, key, value)
    if text is None:
        return None
    if text not in _OBSERVATION_CODES:
        raise ValueError(f"fid {fid}: unknown {key} {text!r}")
    return _OBSERVATION_CODES[text]


def _parse_date(fid: int, key: str, value: object) -> object:
    text = _text_or_none(fid, key, value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, _DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(f"fid {fid}: unparseable {key} {text!r}") from exc


def _parse_gps(fid: int, key: str, value: object) -> object:
    text = _text_or_none(fid, key, value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"fid {fid}: {key} {text!r} is not a number") from exc
    if not math.isfinite(number):
        raise ValueError(f"fid {fid}: {key} {text!r} is not finite")
    return number


_NORMALIZERS: Final[Mapping[str, _Normalizer]] = {
    "survey_obs_type": _observation_code,
    "survey_date": _parse_date,
    "survey_gps_lat": _parse_gps,
    "survey_gps_long": _parse_gps,
}
