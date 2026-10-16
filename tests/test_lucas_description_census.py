import hashlib

import pytest
from shapely.geometry import box

from osm_polygon_lucas import LUCAS_C3_LEVELS, assess_osm_polygon
from tools.lucas_description_census import (
    CoverageDecision,
    _asset_signature,
    _require_asset_signature,
    _status_counts_match_manifest,
    _verify_lucas_asset,
    count_eligible_sentences,
    coverage_decision,
)

OSM = box(0.0, 0.0, 10.0, 10.0)


def test_lucas_asset_verification_rejects_same_size_replacement(tmp_path):
    asset = tmp_path / "lucas.gpkg"
    expected = b"asset"
    asset.write_bytes(expected)
    config = {
        "size_bytes": len(expected),
        "sha256": hashlib.sha256(expected).hexdigest(),
    }

    assert _verify_lucas_asset(asset, config) == config["sha256"]

    asset.write_bytes(b"other")
    with pytest.raises(ValueError, match="SHA-256"):
        _verify_lucas_asset(asset, config)


def test_lucas_asset_identity_detects_path_replacement(tmp_path):
    asset = tmp_path / "lucas.gpkg"
    asset.write_bytes(b"asset")
    signature = _asset_signature(asset)
    replacement = tmp_path / "replacement.gpkg"
    replacement.write_bytes(b"asset")

    replacement.replace(asset)

    with pytest.raises(ValueError, match="changed during the census"):
        _require_asset_signature(asset, signature)


def test_manifest_status_counts_allow_explicit_zero_categories():
    assert _status_counts_match_manifest(
        {"yes": 4}, {"failed": 0, "no": 0, "skipped": 0, "yes": 4}
    )
    assert not _status_counts_match_manifest(
        {"yes": 4}, {"failed": 0, "no": 1, "skipped": 0, "yes": 3}
    )


def test_full_area_gate_yields_reference_code_and_below_gate_is_excluded(make_support):
    at_gate = assess_osm_polygon(
        OSM,
        [make_support("at-gate", "B11", box(0.0, 0.0, 8.0, 10.0))],
        LUCAS_C3_LEVELS,
    )
    below_gate = assess_osm_polygon(
        OSM,
        [make_support("below-gate", "B11", box(0.0, 0.0, 7.9, 10.0))],
        LUCAS_C3_LEVELS,
    )

    assert coverage_decision(at_gate) == CoverageDecision(
        "eligible", "B00", 0.8, ("B11",)
    )
    assert coverage_decision(below_gate) == CoverageDecision(
        "below_threshold", "B00", 0.79, ("B11",)
    )


def test_ambiguous_unknown_and_spatial_absence_are_excluded(make_support):
    ambiguous = assess_osm_polygon(
        OSM,
        [
            make_support("crop", "B11", box(0.0, 0.0, 9.0, 10.0)),
            make_support("wood", "C10", box(1.0, 0.0, 10.0, 10.0)),
        ],
        LUCAS_C3_LEVELS,
    )
    unknown = assess_osm_polygon(
        OSM,
        [
            make_support("mapped", "B11", box(0.0, 0.0, 7.0, 10.0)),
            make_support("unmapped", "-1", box(9.0, 0.0, 10.0, 10.0)),
        ],
        LUCAS_C3_LEVELS,
    )
    boundary = assess_osm_polygon(
        OSM,
        [make_support("boundary", "B11", box(10.0, 0.0, 12.0, 10.0))],
        LUCAS_C3_LEVELS,
    )
    no_overlap = assess_osm_polygon(OSM, [], LUCAS_C3_LEVELS)

    assert coverage_decision(ambiguous).outcome == "ambiguous"
    assert coverage_decision(unknown).outcome == "unknown_unmapped_support"
    assert coverage_decision(boundary).outcome == "boundary_only"
    assert coverage_decision(no_overlap).outcome == "no_overlap"


def test_below_threshold_polygon_cannot_contribute_source_sentence_counts():
    text_units = [
        {
            "description_identity": "failed-only-yes",
            "tag_key": "description",
            "sentence_index": 0,
            "polygon_ids": ["below-threshold-poly"],
        },
        {
            "description_identity": "failed-only-no",
            "tag_key": "description",
            "sentence_index": 0,
            "polygon_ids": ["below-threshold-poly"],
        },
    ]
    labels = [
        {
            "description_identity": "failed-only-yes",
            "tag_key": "description",
            "sentence_index": 0,
            "status": "yes",
        },
        {
            "description_identity": "failed-only-no",
            "tag_key": "description",
            "sentence_index": 0,
            "status": "no",
        },
    ]

    result = count_eligible_sentences(
        {}, text_units, labels, polygon_ids={"below-threshold-poly"}
    )

    assert result["by_lucas_reference_code"] == {}
    assert result["unique_eligible_sentence_instances_by_source_relevance"] == {
        "yes": 0,
        "no": 0,
    }
    assert result["joined_to_eligible_polygons"] == {}
    assert result["not_joined_to_eligible_polygons"] == {"no": 1, "yes": 1}


def test_sentence_counts_are_grouped_by_reference_code_separately_from_relevance():
    eligible = {"crop-poly": "B00", "wood-poly": "C00"}
    text_units = [
        {
            "description_identity": "crop",
            "tag_key": "description",
            "sentence_index": 0,
            "polygon_ids": ["crop-poly"],
        },
        {
            "description_identity": "wood",
            "tag_key": "description",
            "sentence_index": 0,
            "polygon_ids": ["wood-poly"],
        },
        {
            "description_identity": "both",
            "tag_key": "description",
            "sentence_index": 0,
            "polygon_ids": ["crop-poly", "wood-poly"],
        },
    ]
    labels = [
        {
            "description_identity": "crop",
            "tag_key": "description",
            "sentence_index": 0,
            "status": "yes",
        },
        {
            "description_identity": "wood",
            "tag_key": "description",
            "sentence_index": 0,
            "status": "no",
        },
        {
            "description_identity": "both",
            "tag_key": "description",
            "sentence_index": 0,
            "status": "yes",
        },
    ]

    result = count_eligible_sentences(
        eligible, text_units, labels, polygon_ids=set(eligible)
    )

    assert result["by_lucas_reference_code"] == {
        "B00": {
            "linked_text_unit_rows": 2,
            "source_relevance_yes": 2,
            "source_relevance_no": 0,
            "non_binary_statuses": {},
        },
        "C00": {
            "linked_text_unit_rows": 2,
            "source_relevance_yes": 1,
            "source_relevance_no": 1,
            "non_binary_statuses": {},
        },
    }
    assert result["unique_eligible_sentence_instances_by_source_relevance"] == {
        "yes": 2,
        "no": 1,
    }
    assert result["text_units_linked_to_multiple_reference_codes"] == 1
