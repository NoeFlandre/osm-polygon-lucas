import json
from hashlib import sha256
from types import SimpleNamespace
from typing import cast

import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from osm_polygon_lucas import (
    PointObservation,
    PointPolygonStatus,
    SourcePolygon,
    evaluate_point_census,
)


def observation(
    source_id,
    *,
    longitude="0.5",
    latitude="0.5",
    label="B11",
    mode=1,
    year=2022,
    copernicus=1,
    completed=1,
    point_id="point-1",
):
    return PointObservation(
        source_feature_id=source_id,
        point_id=point_id,
        survey_year=year,
        point_copernicus=copernicus,
        surveycprncando=completed,
        survey_obs_type=mode,
        surveycprnlc=label,
        survey_gps_lat=latitude,
        survey_gps_long=longitude,
    )


def polygon(polygon_id="osm:way/1", geometry=None):
    return SourcePolygon(
        polygon_id, geometry if geometry is not None else box(0, 0, 2, 2)
    )


def test_same_main_category_keeps_unique_polygon_and_reports_all_observation_modes():
    census = evaluate_point_census(
        [polygon()],
        [
            observation("1", label="B11", mode=1),
            observation("2", label="B12", mode=2, longitude="1.5"),
            observation(
                "3",
                label="B11",
                mode="Photo-interpretation in the field",
                longitude="1.0",
            ),
            observation("4", label="B11", mode=1, longitude="0.75"),
        ],
    )

    assert census.source_polygon_count == 1
    assert census.eligible_polygon_count == 1
    assert census.taxonomy_level == "main_category"
    assert census.valid_labeled_observations == 4
    assert census.quality_valid_observations == 4
    assert census.located_observations == 4
    assert census.input_observation_modes == {
        "field_photo_interpretation": 1,
        "field_visible_over_100m": 1,
        "field_visible_within_100m": 2,
    }
    assert census.quality_valid_observation_modes == census.input_observation_modes
    assert census.located_observation_modes == {
        "field_photo_interpretation": 1,
        "field_visible_over_100m": 1,
        "field_visible_within_100m": 2,
    }
    assert census.eligible_polygon_labels == {"osm:way/1": "B00"}
    assert census.taxonomy_mapping_sha256 == _main_category_digest()
    decision = census.decisions[0]
    assert decision.valid_observations == 4
    assert decision.observation_mode_counts == census.located_observation_modes
    assert decision.observed_labels == ("B00",)
    assert (decision.anchor_longitude, decision.anchor_latitude) == (1.0, 1.0)


def _main_category_digest():
    from osm_polygon_lucas.lucas_taxonomy import LUCAS_C3_LEVELS

    level = LUCAS_C3_LEVELS[0]
    payload = json.dumps(
        {"name": level.name, "labels": dict(sorted(level.labels.items()))},
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def test_conflicting_labels_exclude_polygon_at_evaluated_taxonomy_level():
    observations = [observation("1", label="B11"), observation("2", label="C10")]

    main = evaluate_point_census([polygon()], observations)
    second_level = evaluate_point_census(
        [polygon()], observations, taxonomy_level="second_level"
    )

    assert main.decisions[0].status is PointPolygonStatus.CONFLICTING_LABELS
    assert main.conflict_polygon_count == 1
    assert main.eligible_polygon_labels == {}
    assert second_level.decisions[0].status is PointPolygonStatus.CONFLICTING_LABELS


def test_same_main_label_can_conflict_at_second_level():
    main = evaluate_point_census(
        [polygon()], [observation("1", label="B11"), observation("2", label="B21")]
    )
    second_level = evaluate_point_census(
        [polygon()],
        [observation("1", label="B11"), observation("2", label="B21")],
        taxonomy_level="second_level",
    )

    assert main.eligible_polygon_labels == {"osm:way/1": "B00"}
    assert second_level.conflict_polygon_count == 1


def test_identical_duplicate_source_record_is_skipped_without_inflating_counts():
    row = observation("same-fid")

    census = evaluate_point_census([polygon()], [row, row, row])

    assert census.observation_count == 3
    assert census.duplicate_records_skipped == 2
    assert census.located_observations == 1
    assert census.eligible_polygon_count == 1


def test_conflicting_duplicate_source_record_id_is_a_data_error():
    with pytest.raises(ValueError, match="conflicting observation data"):
        evaluate_point_census(
            [polygon()],
            [
                observation("same-fid", label="B11"),
                observation("same-fid", label="C10"),
            ],
        )


@pytest.mark.parametrize("source_id", ["", cast(str, 17)])
def test_observation_source_id_must_be_nonempty_text(source_id):
    with pytest.raises(ValueError, match="non-empty text"):
        evaluate_point_census([polygon()], [observation(source_id)])


def test_same_lucas_point_id_can_contribute_distinct_observations_and_conflict():
    census = evaluate_point_census(
        [polygon()],
        [
            observation("fid-1", point_id="duplicate-point", label="B11"),
            observation("fid-2", point_id="duplicate-point", label="C10"),
        ],
    )

    assert census.conflict_polygon_count == 1
    assert census.decisions[0].located_observations == 2


def test_boundary_outside_and_multiple_matches_are_counted_and_excluded():
    polygons = [
        polygon("left", box(0, 0, 1, 1)),
        polygon("right", box(1, 0, 2, 1)),
        polygon("overlap-a", box(3, 0, 5, 2)),
        polygon("overlap-b", box(4, 0, 6, 2)),
    ]
    census = evaluate_point_census(
        polygons,
        [
            observation("boundary", longitude="1", latitude="0.5"),
            observation("boundary-again", longitude="0", latitude="0.5"),
            observation("outside", longitude="9", latitude="9"),
            observation("outside-again", longitude="9", latitude="9"),
            observation("overlap", longitude="4.5", latitude="1"),
        ],
    )

    assert census.boundary_observations == 2
    assert census.outside_observations == 2
    assert census.multiple_match_observations == 2
    assert census.rejection_counts == {
        "boundary_point": 2,
        "multiple_source_polygons": 2,
        "outside_source_polygons": 2,
    }
    assert census.decisions == ()


def test_single_polygon_boundary_is_not_reported_as_a_multiple_match():
    census = evaluate_point_census(
        [polygon(geometry=box(0, 0, 1, 1))],
        [observation("boundary", longitude="0", latitude="0.5")],
    )

    assert census.boundary_observations == 1
    assert census.multiple_match_observations == 0
    assert census.rejection_counts == {"boundary_point": 1}


def test_missing_label_is_reported_but_one_valid_consistent_label_keeps_polygon():
    census = evaluate_point_census(
        [polygon()],
        [
            observation("missing", label=None),
            observation("empty", label=""),
            observation("valid", label="B11"),
        ],
    )

    decision = census.decisions[0]
    assert decision.status is PointPolygonStatus.ELIGIBLE
    assert decision.located_observations == 3
    assert decision.valid_observations == 1
    assert decision.missing_label_observations == 2
    assert census.rejection_counts["missing_label"] == 2


def test_polygon_with_only_missing_or_unmapped_labels_has_no_valid_class():
    census = evaluate_point_census(
        [polygon()],
        [
            observation("missing", label=None),
            observation("unmapped-1", label="Z99"),
            observation("unmapped-2", label="Y99"),
        ],
    )

    assert census.no_valid_label_polygon_count == 1
    assert census.decisions[0].status is PointPolygonStatus.NO_VALID_LABEL
    assert census.decisions[0].unmapped_label_observations == 2
    assert census.rejection_counts["unmapped_label"] == 2


def test_malformed_label_is_reported_for_a_located_polygon():
    census = evaluate_point_census(
        [polygon()],
        [observation("invalid-1", label=17), observation("invalid-2", label=[])],
    )

    assert census.no_valid_label_polygon_count == 1
    assert census.decisions[0].invalid_label_observations == 2
    assert census.rejection_counts["invalid_label"] == 2


def test_quality_flags_and_coordinates_are_explicit_rejections():
    census = evaluate_point_census(
        [polygon()],
        [
            observation("wrong-year", year=2021),
            observation("not-copernicus", copernicus=0),
            observation("invalid-copernicus", copernicus="1"),
            observation("not-completed", completed=0),
            observation("missing-year", year=None),
            observation("unknown-mode", mode="new survey mode"),
            observation("unknown-mode-again", mode="new survey mode"),
            observation("missing-mode", mode=None),
            observation("boolean-mode", mode=True),
            observation("missing-gps", longitude=None),
            observation("invalid-gps", latitude="NaN"),
            observation("invalid-text-gps", latitude="not-a-number"),
            observation("boolean-gps", longitude=True),
            observation("outside-latitude", latitude="91"),
            observation("outside-longitude", longitude="181"),
        ],
    )

    assert census.quality_valid_observations == 0
    assert census.input_observation_modes == {"unknown_or_missing": 4}
    assert census.rejection_counts == {
        "invalid_gps_coordinates": 5,
        "invalid_point_copernicus": 1,
        "missing_survey_obs_type": 1,
        "missing_survey_year": 1,
        "missing_gps_coordinates": 1,
        "not_point_copernicus": 1,
        "not_surveycprncando": 1,
        "unknown_survey_obs_type": 3,
        "wrong_survey_year": 1,
    }


def test_all_points_outside_are_not_assigned_to_nearest_polygon():
    census = evaluate_point_census(
        [polygon()], [observation("outside", longitude="10", latitude="10")]
    )

    assert census.outside_observations == 1
    assert census.eligible_polygon_count == 0
    assert census.rejection_counts["outside_source_polygons"] == 1


def test_no_source_polygons_means_valid_gps_is_counted_as_outside():
    census = evaluate_point_census([], [observation("outside")])

    assert census.source_polygon_count == 0
    assert census.outside_observations == 1


@pytest.mark.parametrize(
    "geometry",
    [
        box(-180, 0, -179, 1),
        box(179, 0, 180, 1),
        box(0, -90, 1, -89),
        box(0, 89, 1, 90),
    ],
)
def test_crs84_extreme_bounds_are_inclusive(geometry):
    census = evaluate_point_census([polygon("edge", geometry)], [])

    assert census.source_polygon_count == 1


@pytest.mark.parametrize(
    "geometry",
    [
        box(-180.5, 0, -179.5, 1),
        box(180, 0, 180.5, 1),
        box(0, -90.5, 1, -90),
        box(0, 90, 1, 90.5),
        box(181, 0, 182, 1),
        box(0, 91, 1, 92),
    ],
)
def test_crs84_bounds_outside_any_axis_are_rejected(geometry):
    with pytest.raises(ValueError, match="edge: geometry is outside OGC:CRS84 bounds"):
        evaluate_point_census([polygon("edge", geometry)], [])


def test_gps_longitude_and_latitude_limits_are_inclusive():
    observations = [
        observation("west", longitude="-180", latitude="0"),
        observation("east", longitude="180", latitude="0"),
        observation("south", longitude="0", latitude="-90"),
        observation("north", longitude="0", latitude="90"),
        observation("west-out", longitude="-180.1", latitude="0"),
        observation("east-out", longitude="180.1", latitude="0"),
        observation("south-out", longitude="0", latitude="-90.1"),
        observation("north-out", longitude="0", latitude="90.1"),
    ]
    census = evaluate_point_census(
        [polygon(geometry=box(-180, -90, 180, 90))], observations
    )

    assert census.quality_valid_observations == 4
    assert census.boundary_observations == 4
    assert census.rejection_counts["invalid_gps_coordinates"] == 4


def test_nonfinite_geometry_error_keeps_polygon_identity():
    from osm_polygon_lucas.point_coverage import _validate_crs84_geometry

    geometry = cast(BaseGeometry, SimpleNamespace(bounds=(float("nan"), 0, 1, 1)))
    with pytest.raises(
        ValueError, match="polygon-nan: geometry has non-finite OGC:CRS84 bounds"
    ):
        _validate_crs84_geometry("polygon-nan", geometry)


def test_duplicate_source_polygon_ids_and_non_geographic_bounds_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        evaluate_point_census([polygon(), polygon()], [])
    with pytest.raises(
        ValueError, match="too-east: geometry is outside OGC:CRS84 bounds"
    ):
        evaluate_point_census([polygon("too-east", box(181, 0, 182, 1))], [])
    with pytest.raises(ValueError, match="non-empty and unique"):
        evaluate_point_census([SourcePolygon(cast(str, 17), box(0, 0, 1, 1))], [])


def test_unknown_taxonomy_level_is_rejected():
    with pytest.raises(ValueError, match="unknown LUCAS C3 taxonomy level"):
        evaluate_point_census([polygon()], [], taxonomy_level="unfrozen-level")
