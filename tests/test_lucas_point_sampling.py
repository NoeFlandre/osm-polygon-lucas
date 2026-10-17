from typing import Any

import pytest
from shapely.geometry import box

from osm_polygon_lucas import PointObservation, SourcePolygon, evaluate_point_census
from tools.lucas_point_sampling import sample_point_sentence_instances


def observation(source_id, longitude, latitude, label):
    return PointObservation(
        source_feature_id=source_id,
        point_id=source_id,
        survey_year=2022,
        point_copernicus=1,
        surveycprncando=1,
        survey_obs_type=1,
        surveycprnlc=label,
        survey_gps_lat=latitude,
        survey_gps_long=longitude,
    )


def census():
    polygons = [
        SourcePolygon("polygon-a", box(9.0, 55.0, 9.1, 55.1)),
        SourcePolygon("polygon-b", box(-3.0, 40.0, -2.9, 40.1)),
    ]
    observations = [
        observation("a", "9.05", "55.05", "C10"),
        observation("b", "-2.95", "40.05", "E10"),
    ]
    return evaluate_point_census(polygons, observations)


def test_balanced_sample_is_deterministic_and_groups_sentences_by_polygon():
    candidates = [
        {"sentence_id": "yes-1", "status": "yes", "polygon_ids": ["polygon-a"]},
        {"sentence_id": "yes-1", "status": "yes", "polygon_ids": ["polygon-a"]},
        {"sentence_id": "yes-2", "status": "yes", "polygon_ids": ["polygon-a"]},
        {
            "sentence_id": "yes-3",
            "status": "yes",
            "polygon_ids": ["polygon-b", "polygon-a"],
        },
        {"sentence_id": "no-1", "status": "no", "polygon_ids": ["polygon-b"]},
        {"sentence_id": "no-2", "status": "no", "polygon_ids": ["polygon-b"]},
        {"sentence_id": "maybe", "status": "skipped", "polygon_ids": ["polygon-a"]},
        {"sentence_id": "unlinked", "status": "yes", "polygon_ids": ["other"]},
    ]

    first = sample_point_sentence_instances(
        candidates,
        census(),
        source="Description",
        target_per_label=3,
        max_per_cell=3,
    )
    second = sample_point_sentence_instances(
        candidates,
        census(),
        source="Description",
        target_per_label=3,
        max_per_cell=3,
    )

    assert first == second
    assert first.eligible_by_label == {"yes": 3, "no": 2}
    assert first.selected_by_label == {"yes": 3, "no": 2}
    assert first.shortfall_by_label == {"yes": 0, "no": 1}
    assert first.unique_eligible_polygons == 2
    assert first.unique_selected_polygons == 2
    assert first.repeated_source_rows_skipped == 1
    assert first.multi_polygon_sentences_anchored == 1
    assert first.non_binary_statuses == {"skipped": 1}
    assert first.selected_sentence_ids_by_polygon.keys() == {"polygon-a", "polygon-b"}
    assert "yes-3" in first.selected_sentence_ids_by_polygon.get("polygon-a", ())


def test_geographic_cell_cap_reports_a_real_class_shortfall():
    sample = sample_point_sentence_instances(
        [
            {"sentence_id": "yes", "status": "yes", "polygon_ids": ["polygon-a"]},
            {"sentence_id": "no", "status": "no", "polygon_ids": ["polygon-a"]},
        ],
        census(),
        source="Website",
        target_per_label=1,
        max_per_cell=1,
    )

    assert sample.selected_by_label == {"yes": 1, "no": 0}
    assert sample.shortfall_by_label == {"yes": 0, "no": 1}
    assert sample.occupied_cells == 1
    assert sample.selected_cells == 1


def test_duplicate_sentence_id_with_different_source_data_is_rejected():
    with pytest.raises(ValueError, match="conflicting source rows"):
        sample_point_sentence_instances(
            [
                {"sentence_id": "s1", "status": "yes", "polygon_ids": ["polygon-a"]},
                {"sentence_id": "s1", "status": "no", "polygon_ids": ["polygon-a"]},
            ],
            census(),
            source="Wiki",
            target_per_label=1,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source": ""}, "source"),
        ({"target_per_label": 0}, "target_per_label"),
        ({"cell_size_metres": 0}, "cell_size_metres"),
        ({"max_per_cell": 0}, "max_per_cell"),
    ],
)
def test_sampling_options_must_be_valid(kwargs, message):
    options: dict[str, Any] = {"source": "Description", **kwargs}
    with pytest.raises(ValueError, match=message):
        sample_point_sentence_instances([], census(), **options)


def test_empty_candidate_set_reports_full_shortfall():
    sample = sample_point_sentence_instances(
        [], census(), source="Wiki", target_per_label=3
    )

    assert sample.eligible_by_label == {"yes": 0, "no": 0}
    assert sample.selected_by_label == {"yes": 0, "no": 0}
    assert sample.shortfall_by_label == {"yes": 3, "no": 3}
