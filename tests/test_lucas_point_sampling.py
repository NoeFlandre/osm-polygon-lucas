from collections import Counter, deque
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from shapely.geometry import box

from osm_polygon_lucas import PointObservation, SourcePolygon, evaluate_point_census
from tools.lucas_point_census_domain import PointIndex
from tools.lucas_point_sampling import (
    _Candidate,
    _round_robin_select,
    sample_point_sentence_instances,
)


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


def test_feasible_quotas_reassign_a_shared_cell_to_avoid_shortfall():
    cell_a = (0, 0)
    cell_b = (1, 0)
    yes_a = _Candidate("yes-a", "yes", "polygon-a", cell_a, "a")
    yes_b = _Candidate("yes-b", "yes", "polygon-b", cell_b, "b")
    no_a = _Candidate("no-a", "no", "polygon-a", cell_a, "c")
    selected = _round_robin_select(
        {"yes": [cell_a, cell_b], "no": [cell_a]},
        {
            "yes": {cell_a: deque([yes_a]), cell_b: deque([yes_b])},
            "no": {cell_a: deque([no_a])},
        },
        target_per_label=1,
        max_per_cell=1,
    )

    assert {(entry.relevance, entry.sentence_id, entry.cell) for entry in selected} == {
        ("yes", "yes-b", cell_b),
        ("no", "no-a", cell_a),
    }


def test_infeasible_full_quotas_preserve_the_largest_balanced_sample():
    cell_a = (0, 0)
    cell_b = (1, 0)
    selected = _round_robin_select(
        {"yes": [cell_a, cell_b], "no": [cell_a]},
        {
            "yes": {
                cell_a: deque([_Candidate("yes-a", "yes", "polygon-a", cell_a, "a")]),
                cell_b: deque([_Candidate("yes-b", "yes", "polygon-b", cell_b, "b")]),
            },
            "no": {cell_a: deque([_Candidate("no-a", "no", "polygon-a", cell_a, "c")])},
        },
        target_per_label=2,
        max_per_cell=1,
    )

    assert Counter(entry.relevance for entry in selected) == {"yes": 1, "no": 1}


def test_feasible_sample_remains_round_robin_across_occupied_cells():
    cells = [(index, 0) for index in range(100)]
    queues = {
        label: {
            cell: deque(
                _Candidate(
                    f"{label}-{cell[0]}-{item}",
                    label,
                    f"polygon-{cell[0]}",
                    cell,
                    f"{label}-{cell[0]}-{item}",
                )
                for item in range(20)
            )
            for cell in cells
        }
        for label in ("yes", "no")
    }
    selected = _round_robin_select(
        {"yes": cells, "no": cells},
        queues,
        target_per_label=500,
        max_per_cell=20,
    )

    assert Counter(entry.relevance for entry in selected) == {"yes": 500, "no": 500}
    assert Counter(entry.cell for entry in selected) == dict.fromkeys(cells, 10)


def test_partial_sample_spreads_extra_quota_across_remaining_cells():
    cell_a, cell_b, cell_c = (0, 0), (1, 0), (2, 0)
    cells = [cell_a, cell_b, cell_c]
    queues = {
        "yes": {
            cell: deque(
                _Candidate(
                    f"yes-{cell[0]}-{item}",
                    "yes",
                    f"polygon-{cell[0]}",
                    cell,
                    f"yes-{cell[0]}-{item}",
                )
                for item in range(20)
            )
            for cell in cells
        },
        "no": {
            cell_a: deque(
                _Candidate(f"no-a-{item}", "no", "polygon-0", cell_a, f"no-a-{item}")
                for item in range(5)
            )
        },
    }

    selected = _round_robin_select(
        {"yes": cells, "no": [cell_a]},
        queues,
        target_per_label=10,
        max_per_cell=10,
    )

    assert Counter(entry.relevance for entry in selected) == {"yes": 10, "no": 5}
    assert {entry.cell for entry in selected} == set(cells)


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


@settings(max_examples=60, derandomize=True, deadline=None)
@given(
    yes_count=st.integers(min_value=0, max_value=18),
    no_count=st.integers(min_value=0, max_value=18),
    target=st.integers(min_value=1, max_value=12),
    cell_capacity=st.integers(min_value=1, max_value=10),
)
def test_sampler_preserves_caps_and_is_input_order_invariant(
    yes_count, no_count, target, cell_capacity
):
    candidates = [
        {
            "sentence_id": f"yes-{index}",
            "status": "yes",
            "polygon_ids": ["polygon-a"],
        }
        for index in range(yes_count)
    ] + [
        {
            "sentence_id": f"no-{index}",
            "status": "no",
            "polygon_ids": ["polygon-a"],
        }
        for index in range(no_count)
    ]
    anchors = PointIndex({"polygon-a": (9.05, 55.05)})
    options = {
        "source": "property-test",
        "target_per_label": target,
        "seed": 19,
        "cell_size_metres": 50_000,
        "max_per_cell": cell_capacity,
    }

    sample = sample_point_sentence_instances(candidates, anchors, **options)
    reversed_sample = sample_point_sentence_instances(
        list(reversed(candidates)), anchors, **options
    )

    assert sample == reversed_sample
    selected_ids = [entry.sentence_id for entry in sample.selected]
    assert len(selected_ids) == len(set(selected_ids))
    assert all(entry.relevance in {"yes", "no"} for entry in sample.selected)
    assert all(count <= target for count in sample.selected_by_label.values())
    assert (
        max(Counter(entry.cell for entry in sample.selected).values(), default=0)
        <= cell_capacity
    )
