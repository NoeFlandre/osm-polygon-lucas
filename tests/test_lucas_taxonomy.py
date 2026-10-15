import pytest
from lucas_gpkg_builder import feature, write_lucas_gpkg
from shapely.geometry import box

from osm_polygon_lucas import (
    LUCAS_C3_LEVELS,
    EligibleSupport,
    LevelStatus,
    assess_osm_polygon,
    iter_lucas_2022_supports,
)
from osm_polygon_lucas.lucas_taxonomy import _derive_levels

OSM = box(0.0, 0.0, 10.0, 10.0)
OFFICIAL_C3 = {
    "A00": {"A10": ("A11", "A12", "A13"), "A20": ("A21", "A22"), "A30": ("A30",)},
    "B00": {
        "B10": ("B11", "B12", "B13", "B14", "B15", "B16", "B17", "B18", "B19"),
        "B20": ("B21", "B22", "B23"),
        "B30": ("B31", "B32", "B33", "B34", "B35", "B36", "B37"),
        "B40": ("B41", "B42", "B43", "B44", "B45"),
        "B50": ("B51", "B52", "B53", "B54", "B55"),
        "B70": ("B71", "B72", "B73", "B74", "B75", "B76", "B77"),
        "B80": ("B81", "B82", "B83", "B84"),
    },
    "C00": {
        "C10": ("C10",),
        "C20": ("C21", "C22", "C23"),
        "C30": ("C31", "C32", "C33"),
    },
    "D00": {"D10": ("D10",), "D20": ("D20",)},
    "E00": {"E10": ("E10",), "E20": ("E20",), "E30": ("E30",)},
    "F00": {"F10": ("F10",), "F20": ("F20",), "F30": ("F30",), "F40": ("F40",)},
    "G00": {
        "G10": ("G11", "G12"),
        "G20": ("G21", "G22"),
        "G30": ("G30",),
        "G40": ("G40",),
        "G50": ("G50",),
    },
    "H00": {"H10": ("H11", "H12"), "H20": ("H21", "H22", "H23")},
}


def test_derive_levels_rejects_an_observed_code_listed_twice():
    rows = (("B00", "B10", ("B11",)), ("B00", "B20", ("B11",)))

    with pytest.raises(ValueError, match="B11"):
        _derive_levels(rows)


def test_levels_are_main_category_then_second_level_then_observed_code():
    assert [level.name for level in LUCAS_C3_LEVELS] == [
        "main_category",
        "second_level",
        "observed_code",
    ]


def test_each_level_is_keyed_on_exactly_the_74_official_observed_codes():
    observed = {
        code
        for groups in OFFICIAL_C3.values()
        for codes in groups.values()
        for code in codes
    }

    assert len(observed) == 74
    assert len(LUCAS_C3_LEVELS) == 3
    for level in LUCAS_C3_LEVELS:
        assert set(level.labels) == observed, level.name


@pytest.mark.parametrize(
    ("raw_class", "expected_labels"),
    [
        ("G12", ("G00", "G10", "G12")),
        ("G30", ("G00", "G30", "G30")),
        ("G50", ("G00", "G50", "G50")),
    ],
)
def test_official_water_classes_map_at_every_level(
    make_support,
    raw_class,
    expected_labels,
):
    support = make_support("f1", raw_class, OSM)

    result = assess_osm_polygon(OSM, [support], LUCAS_C3_LEVELS)

    assert [level.winner for level in result.levels] == list(expected_labels)
    assert [level.unmapped_feature_ids for level in result.levels] == [(), (), ()]


def test_every_observed_code_maps_to_its_official_parents_at_each_level():
    expected = [
        (code, main, second, code)
        for main, groups in OFFICIAL_C3.items()
        for second, codes in groups.items()
        for code in codes
    ]
    labels = {level.name: dict(level.labels) for level in LUCAS_C3_LEVELS}

    actual = [
        (
            code,
            labels["main_category"][code],
            labels["second_level"][code],
            labels["observed_code"][code],
        )
        for code, *_ in expected
    ]

    assert actual == expected


@pytest.mark.parametrize(
    "raw_class",
    [
        "B10",
        "A10",
        "G10",
        "H20",
        "B1",
        "B",
        "B111",
        "",
        "-1",
        "Bx1",
        "Bx2",
        "C2",
        "D1",
        "D2",
        "E1",
        "E2",
        "E3",
        "F4",
    ],
)
def test_headings_and_unlisted_classes_stay_unmapped_at_every_level(
    make_support, raw_class
):
    support = make_support("f1", raw_class, OSM)

    result = assess_osm_polygon(OSM, [support], LUCAS_C3_LEVELS)

    assert [level.classes for level in result.levels] == [(), (), ()]
    assert [level.unmapped_feature_ids for level in result.levels] == [
        ("f1",),
        ("f1",),
        ("f1",),
    ]


def test_two_observed_classes_in_one_second_level_meet_the_gate_at_coarser_levels(
    make_support,
):
    supports = [
        make_support("f1", "B11", box(0.0, 0.0, 9.0, 10.0)),
        make_support("f2", "B12", box(0.0, 0.0, 9.0, 10.0)),
    ]

    result = assess_osm_polygon(OSM, supports, LUCAS_C3_LEVELS)

    assert [(level.status, level.winner) for level in result.levels] == [
        (LevelStatus.WINNER, "B00"),
        (LevelStatus.WINNER, "B10"),
        (LevelStatus.AMBIGUOUS, None),
    ]


def test_a_synthetic_geopackage_feeds_the_official_levels_end_to_end(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "support.gpkg",
        [
            feature(1, box(0.0, 0.0, 9.0, 10.0), surveycprnlc="B11"),
            feature(2, box(0.0, 0.0, 9.0, 10.0), surveycprnlc="B12"),
            feature(3, box(0.0, 0.0, 9.0, 10.0), surveycprnlc="-1"),
            feature(
                4,
                box(0.0, 0.0, 9.0, 10.0),
                surveycprnlc="C10",
                survey_obs_type="Photo-interpretation in the field",
            ),
        ],
    )
    supports = [
        result
        for result in iter_lucas_2022_supports(path, bounds=(0.0, 0.0, 10.0, 10.0))
        if isinstance(result, EligibleSupport)
    ]

    result = assess_osm_polygon(OSM, supports, LUCAS_C3_LEVELS)

    assert [support.source_feature_id for support in result.supports] == ["1", "2", "3"]
    assert [(level.status, level.winner) for level in result.levels] == [
        (LevelStatus.WINNER, "B00"),
        (LevelStatus.WINNER, "B10"),
        (LevelStatus.AMBIGUOUS, None),
    ]
    assert [level.unmapped_feature_ids for level in result.levels] == [
        ("3",),
        ("3",),
        ("3",),
    ]
