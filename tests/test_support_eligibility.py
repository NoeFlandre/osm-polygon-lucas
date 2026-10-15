import json
from datetime import date, datetime
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon, box

from osm_polygon_lucas import (
    Coordinates,
    CoreLabels,
    EligibleSupport,
    Rejection,
    RejectionReason,
    classify_support_row,
)

SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "lucas-2022-schema.json"
SCHEMA_FIELDS = [
    field["name"]
    for field in json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))["fields"]
]
SQUARE = box(0.0, 0.0, 1.0, 1.0)
BOWTIE = Polygon([(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)])


def eligible(result: EligibleSupport | Rejection) -> EligibleSupport:
    assert isinstance(result, EligibleSupport), result
    return result


def test_eligible_row_keeps_each_field_under_its_own_name(base_row):
    support = eligible(classify_support_row("feature-1", base_row, SQUARE))

    assert support.source_feature_id == "feature-1"
    assert support.point_id == "900001"
    assert support.support_class == "C10"
    assert support.survey_date == date(2022, 6, 15)
    assert support.core_labels == CoreLabels(
        survey_lc1="B11", survey_lc2=None, survey_lu1="U111", survey_lu2=None
    )
    assert support.geometry.equals(SQUARE)


def test_target_and_observer_coordinates_stay_separate(base_row):
    support = eligible(classify_support_row("feature-1", base_row, SQUARE))

    assert support.target_coordinates == Coordinates(lat=45.5, long=12.5)
    assert support.observer_gps == Coordinates(lat=45.25, long=12.75)


def test_point_core_labels_stay_separate_from_the_support_class(base_row):
    row = {**base_row, "surveycprnlc": "A10", "survey_lc1": "E10"}

    support = eligible(classify_support_row("feature-1", row, SQUARE))

    assert support.support_class == "A10"
    assert support.core_labels.survey_lc1 == "E10"


def test_survey_date_in_2023_is_kept_for_a_2022_survey(base_row):
    row = {**base_row, "survey_date": date(2023, 2, 14)}

    support = eligible(classify_support_row("feature-1", row, SQUARE))

    assert support.survey_date == date(2023, 2, 14)


@pytest.mark.parametrize(
    ("changes", "geometry", "reasons"),
    [
        ({"survey_year": 2021}, SQUARE, (RejectionReason.SURVEY_YEAR,)),
        ({"point_copernicus": 0}, SQUARE, (RejectionReason.POINT_COPERNICUS,)),
        ({"surveycprncando": 0}, SQUARE, (RejectionReason.SURVEY_CPRN_CAN_DO,)),
        ({"survey_obs_type": 2}, SQUARE, (RejectionReason.SURVEY_OBS_TYPE,)),
        ({"surveycprnlc": None}, SQUARE, (RejectionReason.SUPPORT_CLASS,)),
        ({"point_copernicus": True}, SQUARE, (RejectionReason.POINT_COPERNICUS,)),
        (
            {},
            LineString([(0.0, 0.0), (1.0, 1.0)]),
            (RejectionReason.GEOMETRY_NOT_POLYGONAL,),
        ),
        ({}, None, (RejectionReason.GEOMETRY_NOT_POLYGONAL,)),
        ({}, BOWTIE, (RejectionReason.GEOMETRY_INVALID,)),
        ({}, Polygon(), (RejectionReason.GEOMETRY_NOT_POSITIVE_AREA,)),
    ],
    ids=[
        "survey_year",
        "point_copernicus",
        "surveycprncando",
        "survey_obs_type",
        "null_support_class",
        "boolean_code",
        "line_geometry",
        "missing_geometry",
        "self_intersecting_geometry",
        "empty_geometry",
    ],
)
def test_each_criterion_rejects_with_its_own_reason(
    base_row, changes, geometry, reasons
):
    result = classify_support_row("feature-9", {**base_row, **changes}, geometry)

    assert result == Rejection(source_feature_id="feature-9", reasons=reasons)


def test_every_failing_criterion_is_reported_in_criterion_order(base_row):
    row = {**base_row, "survey_year": 2021, "point_copernicus": 0}

    result = classify_support_row("feature-9", row, SQUARE)

    assert result == Rejection(
        source_feature_id="feature-9",
        reasons=(RejectionReason.SURVEY_YEAR, RejectionReason.POINT_COPERNICUS),
    )


def test_row_keys_must_be_the_exact_lower_case_jrc_names(base_row):
    row = dict(base_row)
    row["SURVEY_YEAR"] = row.pop("survey_year")

    with pytest.raises(ValueError, match="survey_year"):
        classify_support_row("feature-1", row, SQUARE)


@pytest.mark.parametrize("key", SCHEMA_FIELDS)
def test_every_schema_field_is_required(base_row, key):
    row = {name: value for name, value in base_row.items() if name != key}

    with pytest.raises(ValueError, match=key):
        classify_support_row("feature-1", row, SQUARE)


@pytest.mark.parametrize("value", ["2023-02-14", datetime(2023, 2, 14)])
def test_survey_date_must_arrive_as_a_date(base_row, value):
    row = {**base_row, "survey_date": value}

    with pytest.raises(TypeError):
        classify_support_row("feature-1", row, SQUARE)


def test_null_date_and_coordinates_are_kept_as_none_not_rejected(base_row):
    row = {
        **base_row,
        "survey_date": None,
        "point_lat": None,
        "survey_gps_lat": None,
    }

    support = eligible(classify_support_row("feature-1", row, SQUARE))

    assert support.survey_date is None
    assert support.target_coordinates == Coordinates(lat=None, long=12.5)
    assert support.observer_gps == Coordinates(lat=None, long=12.75)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("point_id", 1.5),
        ("point_lat", "45.5"),
        ("survey_gps_long", "12.75"),
        ("survey_lc1", 3),
        ("surveycprnlc", 10),
    ],
)
def test_carried_fields_of_the_wrong_type_raise(base_row, field, value):
    row = {**base_row, field: value}

    with pytest.raises(TypeError):
        classify_support_row("feature-1", row, SQUARE)


def test_schema_fixture_lists_exactly_the_fields_the_parser_reads(base_row):
    assert len(SCHEMA_FIELDS) == len(set(SCHEMA_FIELDS)) == len(base_row)
    assert set(SCHEMA_FIELDS) == set(base_row)
    row = {name: base_row[name] for name in SCHEMA_FIELDS}

    support = eligible(classify_support_row("fixture-feature", row, SQUARE))

    assert support.support_class == "C10"
