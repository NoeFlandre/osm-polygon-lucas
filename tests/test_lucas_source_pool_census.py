import json

import pytest
import shapely
from shapely.geometry import Point, Polygon, box

from tools import lucas_source_pool_census as census
from tools.lucas_source_pool_census import (
    _decode_geometry,
    _load_config,
    _load_source_polygons,
    _manifest_path,
    count_eligible_source_sentences,
    method_report,
    run_availability_report,
    sentence_columns,
)


def test_scoped_config_pins_three_sources_and_the_full_area_rule(repository_root):
    config = _load_config(repository_root / "config/lucas-source-pools.json")

    assert set(config["sources"]) == {"Description", "Website", "Wikidata"}
    assert len(config["scope"]["regions"]) == 110
    report = method_report(config)
    assert report["minimum_class_fraction"] == 0.8
    assert report["denominator"] == (
        "full original OSM polygon area after projection; "
        "uncovered area remains in denominator"
    )
    assert report["uncovered_polygons_are_negative"] is False
    assert report["excluded_coverage_outcomes"] == [
        "below_threshold",
        "ambiguous",
        "unknown_unmapped_support",
        "no_overlap",
        "boundary_only",
    ]
    assert report["excluded_geometry_outcomes"] == [
        "invalid_wkb",
        "invalid_geojson",
        "non_polygon_geometry",
        "outside_crs84_bounds",
        "invalid_geometry",
        "zero_area",
        "invalid_after_projection",
        "zero_area_after_projection",
    ]


def test_revision_pins_are_exact_sha1_values_and_reject_the_old_wiki_typo(
    repository_root, tmp_path
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = _load_config(config_path)
    canonical_wiki_pin = "d6fc058ccf4643bc5fe5bfd5c1c0bc403d3da801"
    assert config["sources"]["Wikidata"]["input_revision"] == canonical_wiki_pin
    assert len(canonical_wiki_pin) == 40

    malformed = json.loads(config_path.read_text(encoding="utf-8"))
    malformed["sources"]["Wikidata"]["input_revision"] = (
        "d6fc058ccf4643bc5fe5bfdc5c1c0bc403d3da801"
    )
    malformed_path = tmp_path / "malformed-pins.json"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(ValueError, match="Wikidata input_revision.*40-character"):
        _load_config(malformed_path)


def test_source_join_columns_never_request_sentence_text(repository_root):
    config = _load_config(repository_root / "config/lucas-source-pools.json")
    expected = {
        "Description": ("description_identity", "tag_key", "sentence_index"),
        "Website": ("polygon_id", "field", "sentence_index"),
        "Wikidata": ("sentence_id",),
    }

    for source_name, key_fields in expected.items():
        text_columns, label_columns = sentence_columns(config["sources"][source_name])
        assert text_columns == (*key_fields, "polygon_ids")
        assert label_columns == (*key_fields, "status", "input_revision")
        assert not {"text", "original_text", "sentences"}.intersection(
            (*text_columns, *label_columns)
        )


def test_website_join_uses_polygon_field_and_sentence_index():
    text_units = [
        {
            "polygon_id": "eligible-poly",
            "field": "website",
            "sentence_index": 0,
            "polygon_ids": ["eligible-poly"],
        },
        {
            "polygon_id": "below-poly",
            "field": "website",
            "sentence_index": 0,
            "polygon_ids": ["below-poly"],
        },
    ]
    labels = [
        {
            "polygon_id": "eligible-poly",
            "field": "website",
            "sentence_index": 0,
            "status": "yes",
            "input_revision": "input-pin",
        },
        {
            "polygon_id": "below-poly",
            "field": "website",
            "sentence_index": 0,
            "status": "no",
            "input_revision": "input-pin",
        },
    ]

    result = count_eligible_source_sentences(
        {"eligible-poly": "B00"},
        text_units,
        labels,
        key_fields=("polygon_id", "field", "sentence_index"),
        stream_name="website",
        polygon_ids={"eligible-poly", "below-poly"},
        expected_input_revision="input-pin",
    )

    assert result["unique_eligible_sentence_instances_by_source_relevance"] == {
        "yes": 1,
        "no": 0,
    }
    assert result["joined_to_eligible_polygons"] == {"yes": 1}
    assert result["not_joined_to_eligible_polygons"] == {"no": 1, "yes": 0}


def test_wikidata_join_uses_sentence_id_and_stream_scope():
    text_units = [
        {"sentence_id": "sentence-1", "polygon_ids": ["eligible-poly"]},
        {"sentence_id": "sentence-2", "polygon_ids": ["other-poly"]},
    ]
    labels = [
        {"sentence_id": "sentence-1", "status": "no", "input_revision": "input-pin"},
        {"sentence_id": "sentence-2", "status": "yes", "input_revision": "input-pin"},
    ]

    result = count_eligible_source_sentences(
        {"eligible-poly": "C00"},
        text_units,
        labels,
        key_fields=("sentence_id",),
        stream_name="wikipedia",
        polygon_ids={"eligible-poly", "other-poly"},
        expected_input_revision="input-pin",
    )

    assert result["unique_eligible_sentence_instances_by_source_relevance"] == {
        "yes": 0,
        "no": 1,
    }


def test_sentence_linked_to_two_lucas_codes_counts_once_in_source_pool():
    result = count_eligible_source_sentences(
        {"crop-poly": "B00", "wood-poly": "C00"},
        [{"sentence_id": "sentence-1", "polygon_ids": ["crop-poly", "wood-poly"]}],
        [{"sentence_id": "sentence-1", "status": "yes", "input_revision": "pin"}],
        key_fields=("sentence_id",),
        stream_name="wikipedia",
        polygon_ids={"crop-poly", "wood-poly"},
        expected_input_revision="pin",
    )

    assert result["unique_eligible_sentence_instances_by_source_relevance"] == {
        "yes": 1,
        "no": 0,
    }
    assert result["text_units_linked_to_multiple_reference_codes"] == 1
    assert result["by_lucas_reference_code"]["B00"]["source_relevance_yes"] == 1
    assert result["by_lucas_reference_code"]["C00"]["source_relevance_yes"] == 1


def test_duplicate_text_unit_key_is_rejected_even_if_one_link_is_ineligible():
    eligible_row = {"sentence_id": "same", "polygon_ids": ["eligible-poly"]}
    ineligible_row = {"sentence_id": "same", "polygon_ids": ["other-poly"]}
    with pytest.raises(ValueError, match="duplicate source text-unit key"):
        count_eligible_source_sentences(
            {"eligible-poly": "B00"},
            [eligible_row, ineligible_row],
            [{"sentence_id": "same", "status": "yes", "input_revision": "pin"}],
            key_fields=("sentence_id",),
            stream_name="wikipedia",
            polygon_ids={"eligible-poly", "other-poly"},
            expected_input_revision="pin",
        )


def test_invalid_source_join_is_not_counted_as_negative():
    with pytest.raises(ValueError, match="missing pinned label rows"):
        count_eligible_source_sentences(
            {"eligible-poly": "B00"},
            [{"sentence_id": "eligible", "polygon_ids": ["eligible-poly"]}],
            [],
            key_fields=("sentence_id",),
            stream_name="wikipedia",
            polygon_ids={"eligible-poly"},
            expected_input_revision="pin",
        )


def test_wkb_and_geojson_source_geometry_decoding():
    polygon = box(0, 0, 1, 1)
    assert _decode_geometry(polygon.wkb, "wkb").equals(polygon)
    assert _decode_geometry(shapely.to_geojson(polygon), "geojson").equals(polygon)
    assert (
        _decode_geometry(shapely.to_geojson(Point(0.5, 0.5)), "geojson").geom_type
        == "Point"
    )


def test_empty_atlas_polygon_is_classified_as_zero_area_before_bounds(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        census,
        "_verified_table_file",
        lambda *_args: (tmp_path / "polygons.parquet", {"row_count": 1}),
    )
    monkeypatch.setattr(
        census,
        "_iter_parquet_rows",
        lambda _path, _columns: iter(
            [{"polygon_id": "empty", "geometry": Polygon().wkb}]
        ),
    )

    geometries, polygon_ids, exclusions, decisions, _entry = _load_source_polygons(
        tmp_path, {}, object(), "wkb"
    )

    assert geometries == []
    assert polygon_ids == {"empty"}
    assert exclusions["zero_area"] == 1
    assert decisions == {"empty": "zero_area"}


def test_plan_manifest_paths_keep_wikipedia_and_wikivoyage_streams_separate(tmp_path):
    source = {"source_id": "wiki"}
    plan_index = {
        ("wiki", "labels/wikipedia/sentences/albania-latest.parquet"): {},
        ("wiki", "labels/wikivoyage/sentences/albania-latest.parquet"): {},
    }
    wikipedia_result = _manifest_path(
        tmp_path,
        plan_index,
        source,
        {"labels_path_template": "labels/wikipedia/sentences/{region}-latest.parquet"},
        "albania",
    )
    wikivoyage_result = _manifest_path(
        tmp_path,
        plan_index,
        source,
        {"labels_path_template": "labels/wikivoyage/sentences/{region}-latest.parquet"},
        "albania",
    )
    assert wikipedia_result is not None
    assert wikivoyage_result is not None
    wikipedia, _ = wikipedia_result
    wikivoyage, _ = wikivoyage_result

    assert (
        wikipedia.name
        == "wiki__labels-wikipedia-sentences-albania-latest.manifest.json"
    )
    assert (
        wikivoyage.name
        == "wiki__labels-wikivoyage-sentences-albania-latest.manifest.json"
    )
    assert wikipedia != wikivoyage


def test_census_orchestrator_passes_the_selected_stream_once(
    repository_root, tmp_path, monkeypatch
):
    import hashlib
    import json

    import tools.lucas_source_pool_census as census

    config_path = repository_root / "config/lucas-source-pools.json"
    config = _load_config(config_path)
    source = config["sources"]["Description"]
    plan = {
        "sources": [
            {
                "source_id": source["source_id"],
                "repo_id": source["label_repository"],
                "revision": source["label_revision"],
            }
        ],
        "partitions": [],
        "plan_sha256": "plan-pin",
        "implementation_revision": "implementation-pin",
    }
    atlas_root = tmp_path / "atlas"
    atlas_root.mkdir()
    (atlas_root / "assembly-plan.json").write_text(json.dumps(plan))
    gpkg = tmp_path / "lucas.gpkg"
    gpkg.write_bytes(b"asset")
    lucas_config = tmp_path / "lucas.json"
    lucas_config.write_text(
        json.dumps({"sha256": hashlib.sha256(b"asset").hexdigest(), "size_bytes": 5})
    )
    seen = []

    def run_region(*args):
        seen.append(args)
        return {
            "region": "albania",
            "stream": "description",
            "source_polygon_records": 0,
            "coverage_outcomes": dict.fromkeys(census._COVERAGE_OUTCOMES, 0),
            "geometry_exclusions": dict.fromkeys(census._GEOMETRY_EXCLUSIONS, 0),
            "eligible_source_relevance_counts": {
                "source_label_rows": {},
                "joined_to_eligible_polygons": {},
                "unique_eligible_sentence_instances_by_source_relevance": {
                    "yes": 0,
                    "no": 0,
                },
            },
        }

    monkeypatch.setattr(census, "_projection", lambda: object())
    monkeypatch.setattr(census, "_run_region_stream", run_region)

    report = census.run_source_pool_census(
        atlas_root,
        gpkg,
        config_path,
        lucas_config,
        "Description",
        regions=["albania"],
    )

    assert report["scope"]["selected_streams"] == ["description"]
    assert report["lucas_asset"]["sha256_verified_this_run"] is True
    assert len(seen) == 1
    assert seen[0][2:5] == (
        "albania",
        "description",
        source["streams"][0],
    )


def test_availability_report_counts_manifest_labels_and_missing_regions(
    repository_root, tmp_path
):
    import json

    config = _load_config(repository_root / "config/lucas-source-pools.json")
    config["scope"]["regions"] = ["albania", "austria"]
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    source = config["sources"]["Description"]
    labels_path = "labels/language-v1/data/albania-latest.parquet"
    atlas_root = tmp_path / "atlas"
    atlas_root.mkdir()
    plan = {
        "sources": [
            {
                "source_id": source["source_id"],
                "repo_id": source["label_repository"],
                "revision": source["label_revision"],
            }
        ],
        "partitions": [{"source_id": source["source_id"], "labels_path": labels_path}],
        "plan_sha256": "plan-pin",
    }
    (atlas_root / "assembly-plan.json").write_text(json.dumps(plan))
    manifest_stem = labels_path.replace("/", "-").removesuffix(".parquet")
    manifest = {
        "source_id": source["source_id"],
        "repo_id": source["label_repository"],
        "revision": source["label_revision"],
        "status_counts": {"yes": 2, "no": 1, "skipped": 1},
        "outputs": {"labels": {"row_count": 4, "size_bytes": 80}},
    }
    (atlas_root / f"description__{manifest_stem}.manifest.json").write_text(
        json.dumps(manifest)
    )

    report = run_availability_report(atlas_root, config_path, ["Description"])

    stream = report["sources"]["Description"]["streams"]["description"]
    assert stream["complete_regions"] == 1
    assert stream["expected_regions"] == 2
    assert stream["missing_regions"] == ["austria"]
    assert stream["pre_lucas_source_label_rows"] == {
        "no": 1,
        "skipped": 1,
        "yes": 2,
    }
    assert stream["output_rows"] == {"labels": 4}
    assert stream["raw_yes_no_pool_at_least_500_each"] is False
    assert report["spatial_eligibility"] == (
        "not run; counts are pre-LUCAS availability only"
    )
