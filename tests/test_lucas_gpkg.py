import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from lucas_gpkg_builder import LAYER, feature, gpkg_blob, write_lucas_gpkg
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box

from osm_polygon_lucas import (
    Coordinates,
    CoreLabels,
    EligibleSupport,
    Rejection,
    RejectionReason,
    iter_lucas_2022_supports,
)
from osm_polygon_lucas.lucas_gpkg import _open_read_only

SQUARE = box(0.0, 0.0, 1.0, 1.0)
BOWTIE = Polygon([(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)])


def results(path: Path, **options: Any) -> list[EligibleSupport | Rejection]:
    return list(iter_lucas_2022_supports(path, **options))


def test_eligible_feature_keeps_each_source_value_under_its_own_field(tmp_path):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(7, SQUARE)])

    [support] = results(path)

    assert isinstance(support, EligibleSupport)
    assert support.source_feature_id == "7"
    assert support.point_id == "900001"
    assert support.support_class == "C10"
    assert support.survey_date == date(2022, 9, 4)
    assert support.target_coordinates == Coordinates(lat=45.5, long=12.5)
    assert support.observer_gps == Coordinates(lat=45.25, long=12.75)
    assert support.core_labels == CoreLabels(
        survey_lc1="B11", survey_lc2=None, survey_lu1="U111", survey_lu2=None
    )
    assert support.geometry.equals(SQUARE)


def test_results_follow_fid_order_with_one_result_per_feature(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(3, SQUARE), feature(1, SQUARE), feature(2, SQUARE)],
    )

    assert [result.source_feature_id for result in results(path)] == ["1", "2", "3"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2022-09-04 00:00:00.000", date(2022, 9, 4)),
        ("2023-06-26 00:00:00.000", date(2023, 6, 26)),
    ],
    ids=["2022", "2023"],
)
def test_survey_dates_keep_their_calendar_day_including_2023(tmp_path, text, expected):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(1, SQUARE, survey_date=text)]
    )

    [support] = results(path)

    assert isinstance(support, EligibleSupport)
    assert support.survey_date == expected


def test_null_survey_date_and_observer_latitude_are_kept_as_none(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, SQUARE, survey_date=None, survey_gps_lat=None)],
    )

    [support] = results(path)

    assert isinstance(support, EligibleSupport)
    assert support.survey_date is None
    assert support.observer_gps == Coordinates(lat=None, long=12.75)


def test_only_observation_code_one_is_eligible(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [
            feature(1, SQUARE, survey_obs_type="Field Survey, point visible <= 100 m"),
            feature(2, SQUARE, survey_obs_type="Field Survey, point visible > 100 m"),
            feature(3, SQUARE, survey_obs_type="Photo-interpretation in the field"),
            feature(4, SQUARE, survey_obs_type=None),
        ],
    )

    eligible, *rejected = results(path)

    assert isinstance(eligible, EligibleSupport)
    assert rejected == [
        Rejection(source_feature_id="2", reasons=(RejectionReason.SURVEY_OBS_TYPE,)),
        Rejection(source_feature_id="3", reasons=(RejectionReason.SURVEY_OBS_TYPE,)),
        Rejection(source_feature_id="4", reasons=(RejectionReason.SURVEY_OBS_TYPE,)),
    ]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"survey_year": 2021}, RejectionReason.SURVEY_YEAR),
        ({"point_copernicus": 0}, RejectionReason.POINT_COPERNICUS),
        ({"surveycprncando": 0}, RejectionReason.SURVEY_CPRN_CAN_DO),
        ({"surveycprnlc": None}, RejectionReason.SUPPORT_CLASS),
    ],
    ids=["survey_year", "point_copernicus", "surveycprncando", "null_support_class"],
)
def test_each_attribute_criterion_rejects_with_its_own_reason(tmp_path, change, reason):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(5, SQUARE, **change)])

    assert results(path) == [Rejection(source_feature_id="5", reasons=(reason,))]


@pytest.mark.parametrize(
    ("geometry", "reason"),
    [
        (LineString([(0.0, 0.0), (1.0, 1.0)]), RejectionReason.GEOMETRY_NOT_POLYGONAL),
        (None, RejectionReason.GEOMETRY_NOT_POLYGONAL),
        (BOWTIE, RejectionReason.GEOMETRY_INVALID),
        (Polygon(), RejectionReason.GEOMETRY_NOT_POSITIVE_AREA),
    ],
    ids=["line", "null", "self_intersecting", "empty"],
)
def test_geometry_that_is_not_a_valid_positive_area_polygon_is_rejected(
    tmp_path, geometry, reason
):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(6, geometry)])

    assert results(path) == [Rejection(source_feature_id="6", reasons=(reason,))]


@pytest.mark.parametrize(
    ("options", "match"),
    [
        ({"crs_authority_code": 4326}, "4326|3035"),
        ({"crs_authority": "ESRI"}, "ESRI|EPSG"),
    ],
    ids=["authority_code", "authority"],
)
def test_crs_must_be_epsg_3035_by_both_authority_and_code(tmp_path, options, match):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)], **options)

    with pytest.raises(ValueError, match=match):
        results(path)


def test_bounds_keep_features_whose_boxes_touch_or_overlap_the_window(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [
            feature(1, box(0.0, 0.0, 1.0, 1.0)),
            feature(2, box(10.0, 0.0, 11.0, 1.0)),
            feature(3, box(20.0, 0.0, 21.0, 1.0)),
        ],
    )

    got = results(path, bounds=(0.0, 0.0, 10.0, 10.0))

    assert [result.source_feature_id for result in got] == ["1", "2"]


def test_bounds_are_read_as_minx_miny_maxx_maxy(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, box(0.0, 5.0, 1.0, 6.0)), feature(2, box(3.0, 0.0, 4.0, 1.0))],
    )

    got = results(path, bounds=(0.0, 5.0, 1.0, 6.0))

    assert [result.source_feature_id for result in got] == ["1"]


@pytest.mark.parametrize(
    "bounds",
    [
        (1.0, 0.0, 0.0, 1.0),
        (0.0, 1.0, 1.0, 0.0),
        (0.0, 0.0, float("nan"), 1.0),
        (0.0, 0.0, float("inf"), 1.0),
        (0.0, 0.0, 1.0),
    ],
    ids=["min_x_above_max_x", "min_y_above_max_y", "nan", "infinite", "three_values"],
)
def test_bounds_must_be_four_finite_numbers_in_order(tmp_path, bounds):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])

    with pytest.raises(ValueError):
        results(path, bounds=bounds)


@pytest.mark.parametrize(
    "bounds",
    [
        ("a", "b", "c", "d"),
        (0.0, None, 1.0, 1.0),
        (0.0, 0.0, "1", 1.0),
        (0.0, 0.0, True, 1.0),
        "abcd",
        5,
        (0.0, 0.0, 10**400, 1.0),
    ],
    ids=[
        "strings",
        "none",
        "numeric_text",
        "bool",
        "text_of_four",
        "not_a_sequence",
        "integer_too_large_for_a_float",
    ],
)
def test_malformed_bounds_raise_value_error_not_type_error(tmp_path, bounds):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])

    with pytest.raises(ValueError):
        results(path, bounds=bounds)


def test_bounds_may_be_given_as_a_list(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, box(0.0, 0.0, 1.0, 1.0)), feature(2, box(20.0, 0.0, 21.0, 1.0))],
    )

    got = results(path, bounds=[0.0, 0.0, 10.0, 10.0])

    assert [result.source_feature_id for result in got] == ["1"]


def test_bounds_need_the_layer_rtree_but_plain_reads_do_not(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(1, SQUARE)], with_rtree=False
    )

    assert [result.source_feature_id for result in results(path)] == ["1"]
    with pytest.raises(
        ValueError, match="rtree_l2022_cop_radpoly_radqs_allatr_wcol_geom"
    ):
        results(path, bounds=(0.0, 0.0, 1.0, 1.0))


def test_missing_file_raises_and_is_not_created(tmp_path):
    path = tmp_path / "absent.gpkg"

    with pytest.raises(FileNotFoundError):
        results(path)
    assert not path.exists()


def test_a_file_that_is_not_sqlite_raises_value_error(tmp_path):
    path = tmp_path / "text.gpkg"
    path.write_bytes(b"not a geopackage\n" * 64)

    with pytest.raises(ValueError):
        results(path)


@pytest.mark.parametrize(
    ("options", "match"),
    [
        ({"contents_srs": 4326}, "3035|4326"),
        ({"geometry_type": "POLYGON"}, "MULTISURFACE|POLYGON"),
        ({"omit_columns": ("survey_gps_long",)}, "survey_gps_long"),
        ({"renamed_columns": {"survey_year": "SURVEY_YEAR"}}, "survey_year"),
    ],
    ids=["crs", "geometry_type", "missing_field", "field_name_case"],
)
def test_layer_metadata_and_exact_field_names_are_validated(tmp_path, options, match):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)], **options)

    with pytest.raises(ValueError, match=match):
        results(path)


@pytest.mark.parametrize(
    "blob",
    [
        gpkg_blob(SQUARE, magic=b"XX"),
        gpkg_blob(SQUARE, version=1),
        gpkg_blob(SQUARE, flags=0x23),
        gpkg_blob(SQUARE, srs_id=4326),
        gpkg_blob(SQUARE, flags=0x0B),
        gpkg_blob(SQUARE)[:20],
        b"GP\x00\x01",
        gpkg_blob(SQUARE)[:43],
        gpkg_blob(SQUARE)[:40] + b"\x05" + bytes(4),
    ],
    ids=[
        "magic",
        "version",
        "extended_flag",
        "srs_id",
        "envelope_code",
        "truncated_envelope",
        "short_header",
        "truncated_wkb_header",
        "wkb_byte_order",
    ],
)
def test_header_faults_raise_value_error_naming_the_fid(tmp_path, blob):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(9, blob)])

    with pytest.raises(ValueError, match=r"fid 9\b"):
        results(path)


def test_a_non_blob_geometry_raises_naming_the_fid(tmp_path):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(9, "not a blob")])

    with pytest.raises(ValueError, match=r"fid 9\b"):
        results(path)


@pytest.mark.parametrize("key", ["survey_obs_type", "survey_date", "survey_gps_lat"])
def test_a_blob_in_a_text_field_names_the_fid_and_the_field(tmp_path, key):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(8, SQUARE, **{key: b"45.25"})]
    )

    with pytest.raises(ValueError, match=rf"fid 8\b.*{key} must be text, not bytes"):
        results(path)


@pytest.mark.parametrize("envelope_code", [0, 1, 2, 3, 4])
def test_every_envelope_code_decodes_to_the_same_polygon(tmp_path, envelope_code):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, gpkg_blob(SQUARE, envelope_code=envelope_code))],
    )

    [support] = results(path)

    assert isinstance(support, EligibleSupport)
    assert support.geometry.equals(SQUARE)


def test_point_and_collection_wkb_types_are_2d_non_polygons(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, Point(0.0, 0.0)), feature(2, GeometryCollection([SQUARE]))],
    )

    assert results(path) == [
        Rejection(
            source_feature_id="1", reasons=(RejectionReason.GEOMETRY_NOT_POLYGONAL,)
        ),
        Rejection(
            source_feature_id="2", reasons=(RejectionReason.GEOMETRY_NOT_POLYGONAL,)
        ),
    ]


def test_wkb_type_codes_beyond_the_2d_simple_types_raise(tmp_path):
    blob = gpkg_blob(SQUARE)
    curve = blob[:41] + (8).to_bytes(4, "little") + blob[45:]
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(9, curve)])

    with pytest.raises(ValueError, match=r"fid 9\b"):
        results(path)


def test_zero_width_bounds_select_the_features_that_contain_the_point(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, SQUARE), feature(2, box(5.0, 5.0, 6.0, 6.0))],
    )

    got = results(path, bounds=(0.5, 0.5, 0.5, 0.5))

    assert [result.source_feature_id for result in got] == ["1"]


@pytest.mark.parametrize(
    ("options", "item"),
    [
        ({"data_type": "attributes"}, f"gpkg_contents data_type of {LAYER}"),
        ({"geometry_srs": 4326}, f"srs_id of {LAYER}.geom"),
        ({"z": 1}, f"z of {LAYER}.geom"),
        ({"m": 1}, f"m of {LAYER}.geom"),
    ],
    ids=["data_type", "geometry_srs", "z", "m"],
)
def test_layer_metadata_faults_name_the_item_that_failed(tmp_path, options, item):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)], **options)

    with pytest.raises(ValueError, match=re.escape(item)):
        results(path)


@pytest.mark.parametrize(
    ("options", "item"),
    [
        ({"contents_srs": 4326}, "gpkg_contents srs_id"),
        ({"geometry_type": "POLYGON"}, "geometry_type_name"),
        ({"crs_authority": "ESRI"}, "organization of srs_id"),
        ({"crs_authority_code": 4326}, "organization_coordsys_id of srs_id"),
    ],
    ids=["contents_srs", "geometry_type", "authority", "authority_code"],
)
def test_layer_and_crs_faults_name_the_item_that_failed(tmp_path, options, item):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)], **options)

    with pytest.raises(ValueError, match=item):
        results(path)


def test_the_connection_refuses_writes(tmp_path):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])
    conn = _open_read_only(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE scratch (x INTEGER)")
    finally:
        conn.close()


def test_big_endian_header_and_wkb_decode_to_the_same_polygon(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, gpkg_blob(SQUARE, little_endian=False))],
    )

    [support] = results(path)

    assert isinstance(support, EligibleSupport)
    assert support.geometry.equals(SQUARE)
    assert support.geometry.area == 1.0


def test_three_dimensional_wkb_raises_value_error_naming_the_fid(tmp_path):
    z_square = Polygon(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    )
    blob = gpkg_blob(z_square, output_dimension=3)
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(9, blob)])

    with pytest.raises(ValueError, match=r"fid 9\b"):
        results(path)


def test_truncated_wkb_raises_value_error_naming_the_fid(tmp_path):
    blob = gpkg_blob(SQUARE)[:-10]
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(9, blob)])

    with pytest.raises(ValueError, match=r"fid 9\b"):
        results(path)


def test_unknown_observation_text_raises_naming_the_fid_and_the_value(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(8, SQUARE, survey_obs_type="Field survey, visible")],
    )

    with pytest.raises(ValueError, match=r"fid 8\b.*Field survey, visible"):
        results(path)


@pytest.mark.parametrize("gps", ["north", "nan", "inf"])
def test_non_numeric_or_non_finite_gps_text_raises(tmp_path, gps):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(8, SQUARE, survey_gps_lat=gps)]
    )

    with pytest.raises(ValueError, match=r"fid 8\b"):
        results(path)


@pytest.mark.parametrize(
    "text",
    ["2022-09-04", "04/09/2022 00:00:00.000", "2022-09-04T00:00:00.000"],
)
def test_survey_date_text_outside_the_observed_shape_raises(tmp_path, text):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(8, SQUARE, survey_date=text)]
    )

    with pytest.raises(ValueError, match=r"fid 8\b"):
        results(path)


def test_null_observation_text_is_rejected_not_raised(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(8, SQUARE, survey_obs_type=None)]
    )

    assert results(path) == [
        Rejection(source_feature_id="8", reasons=(RejectionReason.SURVEY_OBS_TYPE,))
    ]


def test_parser_type_faults_surface_as_value_errors_naming_the_fid(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg", [feature(8, SQUARE, point_id=None)]
    )

    with pytest.raises(ValueError, match=r"fid 8\b"):
        results(path)


def test_a_later_bad_row_does_not_stop_the_earlier_results(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(1, SQUARE), feature(2, SQUARE, survey_gps_lat="north")],
    )
    stream = iter_lucas_2022_supports(path)

    first = next(stream)

    assert isinstance(first, EligibleSupport)
    assert first.source_feature_id == "1"
    with pytest.raises(ValueError, match=r"fid 2\b"):
        next(stream)


def test_manifest_observation_strings_are_eligible_only_for_code_one(
    tmp_path,
    repository_root,
):
    manifest_path = repository_root / "config" / "lucas-2022-source.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    crosswalk = manifest["source_verification"][
        "observed_survey_obs_type_to_official_code"
    ]
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [
            feature(index, SQUARE, survey_obs_type=text)
            for index, text in enumerate(crosswalk, start=1)
        ],
    )

    got = results(path)

    assert [
        (result.source_feature_id, isinstance(result, EligibleSupport))
        for result in got
    ] == [(str(index), code == 1) for index, code in enumerate(crosswalk.values(), 1)]
    assert all(
        result.reasons == (RejectionReason.SURVEY_OBS_TYPE,)
        for result in got
        if isinstance(result, Rejection)
    )


@pytest.mark.parametrize(
    ("bounds", "diagnostic"),
    [
        ((2.0, 0.0, 1.0, 1.0), "minx <= maxx"),
        ((0.0, 0.0, 1.0), "bounds must be four finite numbers"),
        ((0.0, 0.0, "1", 1.0), "is not a real number"),
        ((0.0, 0.0, 10**400, 1.0), "too large for a float"),
        ((0.0, 0.0, float("nan"), 1.0), "is not finite"),
    ],
    ids=["reversed", "wrong_arity", "non_numeric", "float_overflow", "non_finite"],
)
def test_invalid_bounds_keep_actionable_diagnostics(tmp_path, bounds, diagnostic):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])

    with pytest.raises(ValueError) as error:
        results(path, bounds=bounds)

    assert diagnostic in str(error.value)


def test_missing_file_error_includes_the_requested_path(tmp_path):
    path = tmp_path / "absent.gpkg"

    with pytest.raises(FileNotFoundError) as error:
        results(path)

    assert str(path) in str(error.value)
    assert not path.exists()


def test_sqlite_open_error_keeps_path_and_cause(tmp_path, monkeypatch):
    import osm_polygon_lucas.lucas_gpkg as loader

    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])

    def fail_connect(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic open failure")

    monkeypatch.setattr(loader.sqlite3, "connect", fail_connect)

    with pytest.raises(ValueError) as error:
        results(path)

    assert str(path) in str(error.value)
    assert "read-only" in str(error.value)
    assert "synthetic open failure" in str(error.value)


def test_sqlite_scan_error_keeps_path_and_cause(tmp_path, monkeypatch):
    import osm_polygon_lucas.lucas_gpkg as loader

    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])

    def fail_select_rows(conn, bounds):
        raise sqlite3.OperationalError("synthetic scan failure")

    monkeypatch.setattr(loader, "_select_rows", fail_select_rows)

    with pytest.raises(ValueError) as error:
        results(path)

    assert str(path) in str(error.value)
    assert "synthetic scan failure" in str(error.value)


@pytest.mark.parametrize(
    ("table", "predicate", "parameters", "item"),
    [
        (
            "gpkg_contents",
            "table_name = ?",
            (LAYER,),
            f"gpkg_contents row for {LAYER}",
        ),
        (
            "gpkg_geometry_columns",
            "table_name = ? AND column_name = 'geom'",
            (LAYER,),
            f"gpkg_geometry_columns row for {LAYER}.geom",
        ),
        (
            "gpkg_spatial_ref_sys",
            "srs_id = ?",
            (3035,),
            "gpkg_spatial_ref_sys row for srs_id 3035",
        ),
    ],
    ids=["contents_row", "geometry_row", "crs_row"],
)
def test_missing_metadata_rows_name_the_expected_item(
    tmp_path, table, predicate, parameters, item
):
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(1, SQUARE)])
    with sqlite3.connect(path) as conn:
        conn.execute(f"DELETE FROM {table} WHERE {predicate}", parameters)

    with pytest.raises(ValueError) as error:
        results(path)

    assert f"GeoPackage has no {item}" in str(error.value)


def test_bad_version_diagnostic_reports_the_version_byte(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [feature(9, gpkg_blob(SQUARE, version=1))],
    )

    with pytest.raises(ValueError, match=re.escape("GeoPackage version 1 is not 0")):
        results(path)


def test_bad_wkb_byte_order_diagnostic_reports_the_offending_byte(tmp_path):
    blob = gpkg_blob(SQUARE)
    invalid_order = blob[:40] + b"\x05" + bytes(4)
    path = write_lucas_gpkg(tmp_path / "support.gpkg", [feature(9, invalid_order)])

    with pytest.raises(ValueError, match=re.escape("WKB byte order 5 is not 0 or 1")):
        results(path)
