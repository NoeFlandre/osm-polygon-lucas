from datetime import date
from math import nextafter

import pytest
from shapely.geometry import LineString, MultiPolygon, Polygon, box

from osm_polygon_lucas import (
    Coordinates,
    CoreLabels,
    LevelStatus,
    Overlap,
    OverlapRelation,
    SupportEvidence,
    TaxonomyLevel,
    assess_osm_polygon,
    validate_osm_polygon,
)

OSM = box(0.0, 0.0, 10.0, 10.0)
COVER = TaxonomyLevel(name="cover", labels={"B11": "Cropland", "C10": "Woodland"})


def test_class_at_exactly_eighty_percent_is_the_winner(make_support):
    supports = [make_support("f1", "B11", box(0.0, 0.0, 8.0, 10.0))]
    level = TaxonomyLevel(name="cover", labels={"B11": "Cropland"})

    decision = assess_osm_polygon(OSM, supports, [level]).levels[0]

    assert decision.status is LevelStatus.WINNER
    assert decision.winner == "Cropland"
    [cropland] = decision.classes
    assert cropland.fraction == 0.8
    assert cropland.meets_gate is True


def test_one_ulp_below_eighty_percent_is_rejected(make_support):
    osm = box(0.0, 0.0, 3.0, 1.0)
    supports = [make_support("f1", "B11", box(0.0, 0.0, 2.4, 1.0))]
    level = TaxonomyLevel(name="cover", labels={"B11": "Cropland"})

    decision = assess_osm_polygon(osm, supports, [level]).levels[0]

    assert decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    assert decision.winner is None
    [cropland] = decision.classes
    assert cropland.fraction == nextafter(0.8, float("-inf"))
    assert cropland.meets_gate is False


def test_class_just_below_eighty_percent_has_no_winner(make_support):
    osm = box(0.0, 0.0, 100.0, 100.0)
    supports = [make_support("f1", "B11", box(0.0, 0.0, 79.0, 100.0))]
    level = TaxonomyLevel(name="cover", labels={"B11": "Cropland"})

    result = assess_osm_polygon(osm, supports, [level])
    decision = result.levels[0]

    assert result.osm_area == 10000.0
    assert decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    assert decision.winner is None
    [cropland] = decision.classes
    assert cropland.covered_area == 7900.0
    assert cropland.fraction == 0.79
    assert cropland.meets_gate is False


def test_same_class_supports_are_unioned_before_measuring(make_support):
    supports = [
        make_support("f1", "B11", box(0.0, 0.0, 5.0, 10.0)),
        make_support("f2", "B11", box(2.0, 0.0, 7.0, 10.0)),
    ]
    level = TaxonomyLevel(name="cover", labels={"B11": "Cropland"})

    decision = assess_osm_polygon(OSM, supports, [level]).levels[0]

    assert decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    [cropland] = decision.classes
    assert cropland.source_feature_ids == ("f1", "f2")
    assert cropland.covered_area == 70.0
    assert cropland.fraction == 0.7
    assert decision.overlaps == (
        Overlap(
            first_feature_id="f1",
            second_feature_id="f2",
            area=30.0,
            relation=OverlapRelation.SAME_CLASS,
        ),
    )


def test_overlap_outside_the_osm_polygon_is_not_recorded(make_support):
    supports = [
        make_support("left", "B11", box(9.0, 0.0, 12.0, 10.0)),
        make_support("right", "C10", box(10.0, 0.0, 13.0, 10.0)),
    ]

    decision = assess_osm_polygon(OSM, supports, [COVER]).levels[0]

    assert decision.overlaps == ()


def test_subsquare_unit_positive_overlap_is_retained(make_support):
    supports = [
        make_support("f1", "B11", box(0.0, 0.0, 1.5, 1.0)),
        make_support("f2", "C10", box(1.0, 0.0, 2.5, 1.0)),
    ]

    decision = assess_osm_polygon(OSM, supports, [COVER]).levels[0]

    assert decision.overlaps == (
        Overlap(
            first_feature_id="f1",
            second_feature_id="f2",
            area=0.5,
            relation=OverlapRelation.CROSS_CLASS,
        ),
    )


def test_distinct_classes_meeting_the_gate_are_ambiguous(make_support):
    supports = [
        make_support("f1", "B11", box(0.0, 0.0, 9.0, 10.0)),
        make_support("f2", "C10", box(1.0, 0.0, 10.0, 10.0)),
    ]

    decision = assess_osm_polygon(OSM, supports, [COVER]).levels[0]

    assert decision.status is LevelStatus.AMBIGUOUS
    assert decision.winner is None
    assert [(c.label, c.fraction, c.meets_gate) for c in decision.classes] == [
        ("Cropland", 0.9, True),
        ("Woodland", 0.9, True),
    ]
    assert decision.overlaps == (
        Overlap(
            first_feature_id="f1",
            second_feature_id="f2",
            area=80.0,
            relation=OverlapRelation.CROSS_CLASS,
        ),
    )


@pytest.mark.parametrize("order", [("f1", "f2"), ("f2", "f1")])
def test_a_tie_at_the_gate_is_ambiguous_in_either_supply_order(make_support, order):
    by_id = {
        "f1": make_support("f1", "B11", box(0.0, 0.0, 8.0, 10.0)),
        "f2": make_support("f2", "C10", box(0.0, 0.0, 8.0, 10.0)),
    }
    supports = [by_id[feature_id] for feature_id in order]

    decision = assess_osm_polygon(OSM, supports, [COVER]).levels[0]

    assert decision.status is LevelStatus.AMBIGUOUS
    assert decision.winner is None
    assert [c.fraction for c in decision.classes] == [0.8, 0.8]


def test_classes_below_the_gate_have_no_winner_even_when_together_they_cover_all(
    make_support,
):
    supports = [
        make_support("f1", "B11", box(0.0, 0.0, 5.0, 10.0)),
        make_support("f2", "C10", box(5.0, 0.0, 10.0, 10.0)),
    ]

    decision = assess_osm_polygon(OSM, supports, [COVER]).levels[0]

    assert decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    assert decision.winner is None
    assert [c.fraction for c in decision.classes] == [0.5, 0.5]
    assert decision.overlaps == ()


def test_boundary_contact_is_evidence_but_adds_no_coverage(make_support):
    supports = [
        make_support("inside", "B11", box(0.0, 0.0, 8.0, 10.0)),
        make_support("edge", "C10", box(10.0, 0.0, 12.0, 10.0)),
        make_support("corner", "E10", box(10.0, 10.0, 12.0, 12.0)),
        make_support("far", "B11", box(20.0, 20.0, 21.0, 21.0)),
    ]
    level = TaxonomyLevel(
        name="cover",
        labels={"B11": "Cropland", "C10": "Woodland", "E10": "Grassland"},
    )

    result = assess_osm_polygon(OSM, supports, [level])
    decision = result.levels[0]

    assert [
        (evidence.source_feature_id, evidence.intersection_area, evidence.boundary_only)
        for evidence in result.supports
    ] == [("inside", 80.0, False), ("edge", 0.0, True), ("corner", 0.0, True)]
    assert decision.winner == "Cropland"
    assert {c.label: c.fraction for c in decision.classes} == {
        "Cropland": 0.8,
        "Woodland": 0.0,
        "Grassland": 0.0,
    }
    assert decision.overlaps == ()


def test_osm_holes_are_excluded_from_the_denominator(make_support):
    osm = Polygon(
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        holes=[[(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]],
    )
    supports = [
        make_support("outer", "B11", box(0.0, 0.0, 10.0, 10.0)),
        make_support("hole", "C10", box(4.0, 4.0, 6.0, 6.0)),
    ]

    result = assess_osm_polygon(osm, supports, [COVER])
    decision = result.levels[0]

    assert result.osm_area == 96.0
    assert decision.winner == "Cropland"
    assert {c.label: c.fraction for c in decision.classes} == {
        "Cropland": 1.0,
        "Woodland": 0.0,
    }
    assert [(e.source_feature_id, e.boundary_only) for e in result.supports] == [
        ("outer", False),
        ("hole", True),
    ]


def test_a_hole_in_a_support_is_uncovered_area(make_support):
    support_with_hole = Polygon(
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        holes=[[(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0)]],
    )
    supports = [make_support("f1", "B11", support_with_hole)]
    level = TaxonomyLevel(name="cover", labels={"B11": "Cropland"})

    decision = assess_osm_polygon(OSM, supports, [level]).levels[0]

    assert decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    [cropland] = decision.classes
    assert cropland.covered_area == 64.0
    assert cropland.fraction == 0.64


def test_multipolygon_osm_uses_the_sum_of_its_parts_as_denominator(make_support):
    osm = MultiPolygon([box(0.0, 0.0, 10.0, 10.0), box(20.0, 0.0, 30.0, 10.0)])
    supports = [
        make_support(
            "both",
            "B11",
            MultiPolygon([box(0.0, 0.0, 10.0, 10.0), box(20.0, 0.0, 30.0, 10.0)]),
        ),
        make_support("first", "C10", box(0.0, 0.0, 10.0, 10.0)),
    ]

    result = assess_osm_polygon(osm, supports, [COVER])
    decision = result.levels[0]

    assert result.osm_area == 200.0
    assert decision.winner == "Cropland"
    assert {c.label: c.fraction for c in decision.classes} == {
        "Cropland": 1.0,
        "Woodland": 0.5,
    }


def test_levels_are_reported_in_supplied_order_with_their_own_labels(make_support):
    supports = [
        make_support("s1", "B11", box(0.0, 0.0, 5.0, 10.0)),
        make_support("s2", "B12", box(5.0, 0.0, 9.0, 10.0)),
    ]
    fine = TaxonomyLevel(name="fine", labels={"B11": "B11", "B12": "B12"})
    coarse = TaxonomyLevel(name="coarse", labels={"B11": "Cropland", "B12": "Cropland"})

    result = assess_osm_polygon(OSM, supports, [fine, coarse])
    fine_decision, coarse_decision = result.levels

    assert [decision.level for decision in result.levels] == ["fine", "coarse"]
    assert fine_decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    assert [(c.label, c.fraction) for c in fine_decision.classes] == [
        ("B11", 0.5),
        ("B12", 0.4),
    ]
    assert coarse_decision.status is LevelStatus.WINNER
    assert coarse_decision.winner == "Cropland"
    [cropland] = coarse_decision.classes
    assert cropland.source_feature_ids == ("s1", "s2")
    assert cropland.fraction == 0.9


def test_raw_class_is_matched_exactly_and_never_by_code_prefix(make_support):
    supports = [make_support("f1", "B11", box(0.0, 0.0, 10.0, 10.0))]
    level = TaxonomyLevel(name="cover", labels={"B1": "Cropland"})

    result = assess_osm_polygon(OSM, supports, [level])
    decision = result.levels[0]

    assert decision.status is LevelStatus.NO_CLASS_MEETS_GATE
    assert decision.classes == ()
    assert decision.unmapped_feature_ids == ("f1",)
    assert result.osm_area == 100.0


def test_unmapped_support_overlapping_a_mapped_one_is_an_unmapped_overlap(
    make_support,
):
    supports = [
        make_support("mapped", "C10", box(0.0, 0.0, 8.0, 10.0)),
        make_support("unmapped", "B11", box(0.0, 0.0, 9.0, 10.0)),
    ]
    level = TaxonomyLevel(name="cover", labels={"C10": "Woodland"})

    decision = assess_osm_polygon(OSM, supports, [level]).levels[0]

    assert decision.winner == "Woodland"
    assert [c.label for c in decision.classes] == ["Woodland"]
    assert decision.unmapped_feature_ids == ("unmapped",)
    assert decision.overlaps == (
        Overlap(
            first_feature_id="mapped",
            second_feature_id="unmapped",
            area=80.0,
            relation=OverlapRelation.UNMAPPED,
        ),
    )


def test_support_evidence_keeps_feature_point_class_date_and_intersection_area(
    make_support,
):
    support = make_support(
        "feature-7",
        "B11",
        box(5.0, 0.0, 15.0, 10.0),
        point_id="point-42",
        survey_date=date(2023, 3, 1),
    )

    result = assess_osm_polygon(OSM, [support], [COVER])

    assert result.supports == (
        SupportEvidence(
            source_feature_id="feature-7",
            point_id="point-42",
            raw_class="B11",
            survey_date=date(2023, 3, 1),
            target_coordinates=Coordinates(lat=45.5, long=12.5),
            observer_gps=Coordinates(lat=45.25, long=12.75),
            core_labels=CoreLabels(
                survey_lc1="B11", survey_lc2=None, survey_lu1="U111", survey_lu2=None
            ),
            intersection_area=50.0,
            boundary_only=False,
        ),
    )


@pytest.mark.parametrize(
    ("osm", "message"),
    [
        (
            Polygon([(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]),
            "must be valid",
        ),
        (
            LineString([(0.0, 0.0), (10.0, 10.0)]),
            "must be a Polygon or MultiPolygon",
        ),
        (Polygon(), "must have positive area"),
    ],
    ids=["self_intersecting", "not_polygonal", "zero_area"],
)
def test_osm_polygon_must_be_polygonal_valid_and_positive_area(osm, message):
    with pytest.raises(ValueError, match=message):
        assess_osm_polygon(osm, [], [COVER])


def test_valid_osm_polygon_may_have_less_than_one_square_unit_of_area():
    osm = box(0.0, 0.0, 0.5, 0.5)

    assert validate_osm_polygon(osm) is osm
