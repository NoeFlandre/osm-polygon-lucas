import pytest
from shapely.geometry import box

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
        ],
    )

    assert census.source_polygon_count == 1
    assert census.eligible_polygon_count == 1
    assert census.valid_labeled_observations == 3
    assert census.located_observations == 3
    assert census.located_observation_modes == {
        "field_photo_interpretation": 1,
        "field_visible_over_100m": 1,
        "field_visible_within_100m": 1,
    }
    assert census.eligible_polygon_labels == {"osm:way/1": "B00"}
    assert len(census.taxonomy_mapping_sha256) == 64


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

    census = evaluate_point_census([polygon()], [row, row])

    assert census.observation_count == 2
    assert census.duplicate_records_skipped == 1
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
            observation("outside", longitude="9", latitude="9"),
            observation("overlap", longitude="4.5", latitude="1"),
        ],
    )

    assert census.boundary_observations == 1
    assert census.outside_observations == 1
    assert census.multiple_match_observations == 2
    assert census.rejection_counts["multiple_source_polygons"] == 2
    assert census.decisions == ()


def test_missing_label_is_reported_but_one_valid_consistent_label_keeps_polygon():
    census = evaluate_point_census(
        [polygon()],
        [observation("missing", label=None), observation("valid", label="B11")],
    )

    decision = census.decisions[0]
    assert decision.status is PointPolygonStatus.ELIGIBLE
    assert decision.located_observations == 2
    assert decision.valid_observations == 1
    assert decision.missing_label_observations == 1
    assert census.rejection_counts["missing_label"] == 1


def test_polygon_with_only_missing_or_unmapped_labels_has_no_valid_class():
    census = evaluate_point_census(
        [polygon()],
        [observation("missing", label=None), observation("unmapped", label="Z99")],
    )

    assert census.no_valid_label_polygon_count == 1
    assert census.decisions[0].status is PointPolygonStatus.NO_VALID_LABEL
    assert census.rejection_counts["unmapped_label"] == 1


def test_malformed_label_is_reported_for_a_located_polygon():
    census = evaluate_point_census([polygon()], [observation("invalid", label=17)])

    assert census.no_valid_label_polygon_count == 1
    assert census.decisions[0].invalid_label_observations == 1
    assert census.rejection_counts["invalid_label"] == 1


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
            observation("missing-mode", mode=None),
            observation("missing-gps", longitude=None),
            observation("invalid-gps", latitude="NaN"),
            observation("invalid-text-gps", latitude="not-a-number"),
            observation("boolean-gps", longitude=True),
            observation("outside-latitude", latitude="91"),
            observation("outside-longitude", longitude="181"),
        ],
    )

    assert census.quality_valid_observations == 0
    assert census.rejection_counts == {
        "invalid_gps_coordinates": 5,
        "invalid_point_copernicus": 1,
        "missing_survey_obs_type": 1,
        "missing_survey_year": 1,
        "missing_gps_coordinates": 1,
        "not_point_copernicus": 1,
        "not_surveycprncando": 1,
        "unknown_survey_obs_type": 1,
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


def test_duplicate_source_polygon_ids_and_non_geographic_bounds_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        evaluate_point_census([polygon(), polygon()], [])
    with pytest.raises(ValueError, match="OGC:CRS84 bounds"):
        evaluate_point_census(
            [polygon(geometry=box(300_000, 5_000_000, 300_010, 5_000_010))], []
        )
    with pytest.raises(ValueError, match="non-empty and unique"):
        evaluate_point_census([SourcePolygon(17, box(0, 0, 1, 1))], [])


def test_unknown_taxonomy_level_is_rejected():
    with pytest.raises(ValueError, match="unknown LUCAS C3 taxonomy level"):
        evaluate_point_census([polygon()], [], taxonomy_level="unfrozen-level")
