import hashlib
import math
from collections import Counter
from types import SimpleNamespace
from typing import cast

import pytest
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from tools.lucas_point_census_domain import (
    _outside_crs84,
    _validate_label_join,
    candidate_partition_report,
    canonical_json,
    collect_candidate_links,
    geometry_exclusion,
    invalid_geometry_digest,
    join_candidate_labels,
    key_from_row,
    make_candidate_rows,
    payload_sha256,
    point_index_from_result,
    validate_text_key_replay,
)


@pytest.mark.parametrize(
    ("geometry", "expected"),
    [
        (Point(0, 0), "non_polygon_geometry"),
        (Polygon(), "zero_area"),
        (Polygon([(0, 0), (0, 1), (0, 2), (0, 0)]), "zero_area"),
        (Polygon([(0, 0), (4, 4), (0, 4), (3, 0), (0, 0)]), "invalid_geometry"),
        (box(-181, 0, -180, 1), "outside_crs84_bounds"),
        (box(0, -91, 1, -90), "outside_crs84_bounds"),
        (box(180, 0, 181, 1), "outside_crs84_bounds"),
        (box(0, 90, 1, 91), "outside_crs84_bounds"),
        (box(-180, -90, 180, 90), None),
    ],
)
def test_geometry_exclusions_cover_shape_and_crs_bounds(geometry, expected):
    assert geometry_exclusion(geometry) == expected


@pytest.mark.parametrize("bounds", [(math.nan, 0, 1, 1), (0, 0, math.inf, 1)])
def test_non_finite_bounds_are_rejected(bounds):
    geometry = cast(BaseGeometry, SimpleNamespace(bounds=bounds))
    assert _outside_crs84(geometry) is True


def test_invalid_geometry_digest_handles_bytes_text_and_other_values():
    assert invalid_geometry_digest(b"wkb", "invalid_wkb") == invalid_geometry_digest(
        "wkb", "invalid_wkb"
    )
    assert invalid_geometry_digest(123, "invalid_geojson") == invalid_geometry_digest(
        123, "invalid_geojson"
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        "not-a-list",
        [],
        ["polygon-a"],
        ["", "polygon-b"],
        [1],
        [math.nan, 0],
    ],
)
def test_point_anchors_require_a_pair_of_finite_crs84_coordinates(value):
    result = {
        "eligible_polygon_anchors": {"polygon-a": value},
        "eligible_polygon_labels": {"polygon-a": "C10"},
    }
    with pytest.raises(ValueError, match="invalid polygon anchors|non-finite"):
        point_index_from_result(result)


@pytest.mark.parametrize(
    ("anchors", "labels", "message"),
    [
        (None, {}, "missing eligible polygon anchors"),
        ({"polygon-a": [1.0, 2.0]}, None, "labels and anchors disagree"),
        ({"polygon-a": [1.0, 2.0]}, {}, "labels and anchors disagree"),
    ],
)
def test_point_index_rejects_incomplete_checkpoint_maps(anchors, labels, message):
    with pytest.raises(ValueError, match=message):
        point_index_from_result(
            {
                "eligible_polygon_anchors": anchors,
                "eligible_polygon_labels": labels,
            }
        )


@pytest.mark.parametrize("coordinates", [[-180.0, -90.0], [180.0, 90.0], [0.0, 0.0]])
def test_point_index_accepts_crs84_boundary_coordinates(coordinates):
    index = point_index_from_result(
        {
            "eligible_polygon_anchors": {"polygon-a": coordinates},
            "eligible_polygon_labels": {"polygon-a": "C10"},
        }
    )
    assert index.eligible_polygon_anchors == {"polygon-a": tuple(coordinates)}


@pytest.mark.parametrize(
    "coordinates",
    [[-180.0001, 0.0], [180.0001, 0.0], [0.0, -90.0001], [0.0, 90.0001]],
)
def test_point_index_rejects_coordinates_outside_crs84_bounds(coordinates):
    with pytest.raises(ValueError, match="outside OGC:CRS84 bounds"):
        point_index_from_result(
            {
                "eligible_polygon_anchors": {"polygon-a": coordinates},
                "eligible_polygon_labels": {"polygon-a": "C10"},
            }
        )


@pytest.mark.parametrize("index", [0, 9])
def test_sentence_key_accepts_non_negative_integer_index(index):
    assert key_from_row({"sentence_index": index}, ("sentence_index",)) == (index,)


@pytest.mark.parametrize("index", [-1, True, "0", None])
def test_sentence_key_rejects_non_integer_or_negative_index(index):
    with pytest.raises(ValueError, match="non-negative integer"):
        key_from_row({"sentence_index": index}, ("sentence_index",))


def test_label_join_checks_row_count_revision_and_complete_candidate_coverage():
    candidate_keys: set[tuple[str, ...]] = {("sentence-a",)}
    source_statuses = Counter(yes=1)
    revisions = {"revision-a"}
    labels: dict[tuple[str, ...], str] = {("sentence-a",): "yes"}

    def validate(
        row_count: int,
        expected_rows: int,
        candidate_revisions: set[str],
        candidate_labels: dict[tuple[str, ...], str],
        keys: set[tuple[str, ...]],
    ) -> None:
        _validate_label_join(
            row_count,
            expected_rows,
            source_statuses,
            candidate_revisions,
            "revision-a",
            candidate_labels,
            keys,
        )

    validate(1, 1, revisions, labels, candidate_keys)

    with pytest.raises(ValueError, match="row count"):
        validate(1, 2, revisions, labels, candidate_keys)
    with pytest.raises(ValueError, match="pinned input revision"):
        validate(1, 1, {"revision-b"}, labels, candidate_keys)
    with pytest.raises(ValueError, match="missing pinned labels"):
        validate(1, 1, revisions, {}, candidate_keys)
    _validate_label_join(
        row_count=0,
        expected_rows=0,
        source_statuses=Counter(),
        revisions=set(),
        expected_revision="revision-a",
        labels={},
        candidate_keys=set(),
    )


def test_canonical_json_and_payload_digest_are_stable_for_unicode_values():
    value = {"é": 1, "a": 2}
    expected = '{"a":2,"é":1}'
    assert canonical_json(value) == expected
    assert payload_sha256(value) == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_label_join_counts_all_rows_and_only_joined_candidates():
    labels, source_statuses, joined_statuses = join_candidate_labels(
        [
            {"key": "candidate-yes", "status": "yes", "input_revision": "r1"},
            {"key": "candidate-no", "status": "no", "input_revision": "r1"},
            {"key": "not-eligible", "status": "yes", "input_revision": "r1"},
        ],
        ("key",),
        {("candidate-yes",), ("candidate-no",)},
        "r1",
        3,
    )
    assert labels == {("candidate-yes",): "yes", ("candidate-no",): "no"}
    assert source_statuses == Counter(yes=2, no=1)
    assert joined_statuses == Counter(yes=1, no=1)


def test_candidate_partition_report_and_rows_keep_public_schema():
    links = {("sentence-a",): ["polygon-a"], ("sentence-b",): ["polygon-b"]}
    labels = {("sentence-a",): "yes", ("sentence-b",): "no"}
    candidates = make_candidate_rows("website", "website", "albania", links, labels)
    assert candidates == [
        {
            "sentence_id": '["website","website",["sentence-a"]]',
            "sentence_key": ["sentence-a"],
            "status": "yes",
            "polygon_ids": ["polygon-a"],
            "origin_region": "albania",
        },
        {
            "sentence_id": '["website","website",["sentence-b"]]',
            "sentence_key": ["sentence-b"],
            "status": "no",
            "polygon_ids": ["polygon-b"],
            "origin_region": "albania",
        },
    ]
    report = candidate_partition_report(
        "albania",
        "website",
        5,
        2,
        Counter(yes=3, no=2),
        Counter(yes=1, no=1),
        candidates,
    )
    assert report == {
        "region": "albania",
        "stream": "website",
        "text_unit_rows_scanned": 5,
        "duplicate_validation_rows": 2,
        "source_label_rows": {"no": 2, "yes": 3},
        "joined_to_point_eligible_polygons": {"no": 1, "yes": 1},
        "not_joined_to_point_eligible_polygons": {"no": 1, "yes": 2},
        "point_eligible_sentence_instances": 2,
        "candidates": candidates,
    }


def test_candidate_text_key_replay_rejects_duplicates_and_row_changes():
    assert (
        validate_text_key_replay(
            [{"key": "candidate"}, {"key": "other"}],
            ("key",),
            {("candidate",)},
            2,
        )
        == 2
    )
    with pytest.raises(
        ValueError, match="duplicate point-eligible source text-unit key"
    ):
        validate_text_key_replay(
            [{"key": "candidate"}, {"key": "candidate"}],
            ("key",),
            {("candidate",)},
            2,
        )
    with pytest.raises(ValueError, match="changed during duplicate-key validation"):
        validate_text_key_replay(
            [{"key": "candidate"}],
            ("key",),
            {("candidate",)},
            2,
        )


def test_candidate_links_validate_lists_and_allow_unlinked_rows():
    candidate_links, row_count = collect_candidate_links(
        [
            {"key": "unlinked", "polygon_ids": []},
            {"key": "eligible", "polygon_ids": ["polygon-a"]},
        ],
        ("key",),
        {"polygon-a"},
        {"polygon-a"},
        "description",
    )
    assert candidate_links == {("eligible",): ["polygon-a"]}
    assert row_count == 2

    for links in ("polygon-a", [""], [1]):
        with pytest.raises(ValueError, match="polygon_ids must be non-empty strings"):
            collect_candidate_links(
                [{"key": "bad", "polygon_ids": links}],
                ("key",),
                {"polygon-a"},
                {"polygon-a"},
                "description",
            )


@pytest.mark.parametrize(
    ("rows", "candidate_keys", "expected_rows", "message"),
    [
        (
            [{"key": "a", "status": "maybe", "input_revision": "r1"}],
            {("a",)},
            1,
            "unsupported status",
        ),
        (
            [{"key": "a", "status": "yes", "input_revision": 1}],
            {("a",)},
            1,
            "revision must be text",
        ),
        (
            [{"key": "a", "status": "yes", "input_revision": "r1"}],
            {("a",)},
            2,
            "row count",
        ),
    ],
)
def test_candidate_label_join_rejects_invalid_rows(
    rows, candidate_keys, expected_rows, message
):
    with pytest.raises(ValueError, match=message):
        join_candidate_labels(rows, ("key",), candidate_keys, "r1", expected_rows)
