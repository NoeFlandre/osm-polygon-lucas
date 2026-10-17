import hashlib
import json
from pathlib import Path

import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st
from lucas_gpkg_builder import feature, write_lucas_gpkg
from shapely.geometry import box

from osm_polygon_lucas.point_coverage import (
    PointCensus,
    PointPolygonDecision,
    PointPolygonStatus,
)
from tools import lucas_point_source_census as point_runner
from tools import lucas_source_pool_census as pool_census
from tools.lucas_point_census_domain import (
    coalesce_partition_candidates,
    point_decision_digest,
    point_result,
)
from tools.lucas_point_sampling import sample_point_sentence_instances
from tools.lucas_point_source_census import (
    _PointIndex,
    _PolygonInventory,
    _read_checkpoint,
    _record_polygon,
    _sampling_candidates,
    _selected_regions,
    _selected_stream_names,
    _validate_seed,
    _validate_separate_path,
    _validate_target_per_label,
    _write_checkpoint,
    run_all_point_source_censuses,
    run_point_source_census,
)

REGION = "albania"
POLYGON_A = box(12.0, 45.0, 13.0, 46.0)
POLYGON_B = box(14.0, 45.0, 15.0, 46.0)


def _snapshot_root(base: Path, repository: str, revision: str) -> Path:
    root = base / f"datasets--{repository.replace('/', '--')}" / "snapshots" / revision
    root.mkdir(parents=True)
    return root


def _write_parquet(root: Path, relative: str, rows: list[dict]) -> Path:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _write_point_gpkg(path: Path) -> None:
    shared = {
        "point_lat": 0.0,
        "point_long": 0.0,
        "survey_lc1": "B11",
        "survey_gps_lat": "45.5",
        "survey_gps_long": "12.5",
    }
    write_lucas_gpkg(
        path,
        [
            feature(1, box(0, 0, 1, 1), point_id=9001, surveycprnlc="C10", **shared),
            feature(2, box(0, 0, 1, 1), point_id=9001, surveycprnlc="C10", **shared),
            feature(
                3,
                box(0, 0, 1, 1),
                point_id=9002,
                surveycprnlc="C10",
                survey_gps_lat="45.5",
                survey_gps_long="14.5",
            ),
            feature(
                4,
                box(0, 0, 1, 1),
                point_id=9003,
                surveycprnlc="B11",
                survey_gps_lat="45.5",
                survey_gps_long="14.5",
            ),
            feature(
                5,
                box(0, 0, 1, 1),
                point_id=9004,
                survey_gps_lat="45.5",
                survey_gps_long="13.0",
            ),
            feature(
                6,
                box(0, 0, 1, 1),
                point_id=9005,
                survey_gps_lat="45.0",
                survey_gps_long="11.5",
            ),
            feature(
                7,
                box(0, 0, 1, 1),
                point_id=9006,
                survey_year=2021,
                survey_gps_lat="45.5",
                survey_gps_long="12.5",
            ),
        ],
    )


def _source_and_label_files(
    source_name: str, source: dict, input_root: Path, label_root: Path
) -> list[Path]:
    geojson_a = shapely.to_geojson(POLYGON_A)
    geojson_b = shapely.to_geojson(POLYGON_B)
    written: list[Path] = []
    if source_name == "Description":
        written.append(
            _write_parquet(
                input_root,
                f"data/{REGION}-latest.parquet",
                [
                    {"osm_type": "way", "osm_id": 1, "geometry": POLYGON_A.wkb},
                    {"osm_type": "way", "osm_id": 2, "geometry": POLYGON_B.wkb},
                ],
            )
        )
        text_rows = [
            {
                "description_identity": "desc-a",
                "osm_type": "way",
                "osm_id": 1,
                "tag_key": "description",
                "sentence_count": 2,
                "sentences": ["private fixture yes", "private fixture no"],
            },
            {
                "description_identity": "desc-b",
                "osm_type": "way",
                "osm_id": 2,
                "tag_key": "description",
                "sentence_count": 1,
                "sentences": ["private fixture conflict"],
            },
        ]
        written.append(
            _write_parquet(
                input_root, f"language-v1/data/{REGION}-latest.parquet", text_rows
            )
        )
        labels = [
            {
                "description_identity": identity,
                "tag_key": "description",
                "sentence_index": index,
                "decision": status,
                "input_revision": source["input_revision"],
            }
            for identity, index, status in (
                ("desc-a", 0, "yes"),
                ("desc-a", 1, "no"),
                ("desc-b", 0, "yes"),
            )
        ]
        written.append(
            _write_parquet(
                label_root,
                f"labels/language-v1/data/{REGION}-latest.parquet",
                labels,
            )
        )
    elif source_name == "Website":
        written.append(
            _write_parquet(
                input_root,
                f"polygons/{REGION}-latest.parquet",
                [
                    {
                        "polygon_id": "poly-a",
                        "geometry": geojson_a,
                        "website_sentence_count": 1,
                        "contact_website_sentence_count": 1,
                        "website_sentences": ["private fixture website"],
                    },
                    {
                        "polygon_id": "poly-b",
                        "geometry": geojson_b,
                        "website_sentence_count": None,
                        "contact_website_sentence_count": 0,
                    },
                ],
            )
        )
        written.append(
            _write_parquet(
                label_root,
                f"labels/polygons/{REGION}-latest.parquet",
                [
                    {
                        "polygon_id": polygon_id,
                        "field": field,
                        "sentence_index": 0,
                        "decision": status,
                        "input_revision": source["input_revision"],
                    }
                    for polygon_id, field, status in (
                        ("poly-a", "website", "yes"),
                        ("poly-a", "contact_website", "no"),
                    )
                ],
            )
        )
    else:
        written.append(
            _write_parquet(
                input_root,
                f"polygons/{REGION}-latest.parquet",
                [
                    {"polygon_id": "poly-a", "geometry": geojson_a},
                    {"polygon_id": "poly-b", "geometry": geojson_b},
                ],
            )
        )
        written.append(
            _write_parquet(
                input_root,
                f"polygon_document_links/{REGION}-latest.parquet",
                [
                    {"document_id": "doc-a", "polygon_id": "poly-a"},
                    {"document_id": "doc-b", "polygon_id": "poly-b"},
                ],
            )
        )
        for stream, status in (("wikipedia", "yes"), ("wikivoyage", "no")):
            written.append(
                _write_parquet(
                    input_root,
                    f"{stream}/sentences/{REGION}-latest.parquet",
                    [
                        {
                            "sentence_id": "shared-sentence-id",
                            "document_id": "doc-a",
                            "text": "private fixture wiki sentence",
                        },
                        {
                            "sentence_id": f"conflict-{stream}",
                            "document_id": "doc-b",
                            "text": "private fixture conflict sentence",
                        },
                    ],
                )
            )
            written.append(
                _write_parquet(
                    label_root,
                    f"labels/{stream}/sentences/{REGION}-latest.parquet",
                    [
                        {
                            "sentence_id": sentence_id,
                            "decision": decision,
                            "input_revision": source["input_revision"],
                        }
                        for sentence_id, decision in (
                            ("shared-sentence-id", status),
                            (f"conflict-{stream}", "yes"),
                        )
                    ],
                )
            )
    return written


def _assert_report_schema(report: dict, source: dict, source_name: str) -> None:
    assert set(report) == {
        "report_schema",
        "source_name",
        "source_id",
        "source_display_label",
        "scope",
        "method",
        "source_pins",
        "lucas_asset",
        "config_hashes",
        "code_and_runtime_identity",
        "input_receipts",
        "point_census",
        "cohorts",
        "checkpointing",
        "sampling_complete_for_selected_scope",
    }
    assert report["report_schema"] == "lucas-point-source-census/v1"
    assert report["source_name"] == source_name
    assert report["source_id"] == source["source_id"]
    assert report["source_display_label"] == source.get("display_label", source_name)
    assert set(report["scope"]) == {
        "regions",
        "configured_region_count",
        "streams",
        "scope_complete",
    }
    assert set(report["method"]) == {
        "point_rule",
        "boundary_outside_multiple_matches",
        "conflicting_or_unlabeled_polygons",
        "source_yes_no_semantics",
        "sampling",
    }
    assert report["method"]["point_rule"] == (
        "actual survey_gps_long/survey_gps_lat observations and "
        "surveycprnlc mapped to main_category; exactly one distinct "
        "mapped category per polygon"
    )
    assert report["method"]["boundary_outside_multiple_matches"] == "excluded"
    assert report["method"]["conflicting_or_unlabeled_polygons"] == "excluded"
    assert report["method"]["source_yes_no_semantics"] == (
        "source relevance labels, separate from LUCAS categories"
    )
    assert set(report["method"]["sampling"]) == {
        "target_per_label",
        "seed",
        "cell_size_metres",
        "max_per_cell",
        "anchor_rule",
    }
    assert report["method"]["sampling"]["anchor_rule"] == (
        "lexically smallest point-eligible linked polygon ID"
    )
    assert report["method"]["sampling"] == {
        "target_per_label": 2,
        "seed": 42,
        "cell_size_metres": 50_000,
        "max_per_cell": 20,
        "anchor_rule": "lexically smallest point-eligible linked polygon ID",
    }
    assert set(report["source_pins"]) == {
        "source_id",
        "input_repository",
        "input_revision",
        "label_repository",
        "label_revision",
        "geometry_encoding",
        "join_key_fields",
    }
    assert report["source_pins"] == {
        key: source[key]
        for key in (
            "source_id",
            "input_repository",
            "input_revision",
            "label_repository",
            "label_revision",
            "geometry_encoding",
            "join_key_fields",
        )
    }
    assert set(report["lucas_asset"]) == {
        "sha256",
        "size_bytes",
        "sha256_verification_checkpoint_reused",
        "identity_stable_during_run",
    }
    assert report["lucas_asset"]["identity_stable_during_run"] is True
    assert set(report["code_and_runtime_identity"]) == {"code_sha256", "runtime"}
    assert set(report["code_and_runtime_identity"]["code_sha256"]) == {
        "point_census_tool",
        "point_census_domain",
        "source_pool_tool",
        "direct_snapshot_tool",
        "point_coverage",
        "point_gpkg",
        "taxonomy",
        "sampling_tool",
    }
    assert set(report["code_and_runtime_identity"]["runtime"]) == {
        "python",
        "pyarrow",
        "pyproj",
        "proj",
        "proj_data",
        "epsg_database",
        "shapely",
        "geos",
    }
    assert all(
        len(digest) == 64
        for digest in report["code_and_runtime_identity"]["code_sha256"].values()
    )
    assert set(report["input_receipts"]) == {"geometry", "partitions"}
    assert set(report["checkpointing"]) == {
        "enabled",
        "directory",
        "point_census_reused",
        "candidate_checkpoints_reused",
        "candidate_checkpoints_saved",
    }
    assert report["checkpointing"]["enabled"] is True
    assert (
        report["sampling_complete_for_selected_scope"]
        is report["scope"]["scope_complete"]
    )

    points = report["point_census"]
    assert set(points) == {
        "source_polygon_records",
        "unique_source_polygon_records",
        "duplicate_polygon_records_coalesced",
        "valid_unique_source_polygons",
        "geometry_exclusions",
        "taxonomy_level",
        "taxonomy_mapping_sha256",
        "observation_count",
        "duplicate_records_skipped",
        "quality_valid_observations",
        "located_observations",
        "valid_labeled_observations",
        "boundary_observations",
        "outside_observations",
        "multiple_match_observations",
        "rejection_counts",
        "input_observation_modes",
        "quality_valid_observation_modes",
        "located_observation_modes",
        "eligible_polygon_count",
        "conflict_polygon_count",
        "no_valid_label_polygon_count",
        "unobserved_valid_polygon_count",
        "point_polygon_status_counts",
        "eligible_polygon_labels",
        "eligible_polygon_anchors",
        "point_decision_sha256",
    }
    assert set(points["geometry_exclusions"]) == {
        "invalid_wkb",
        "invalid_geojson",
        "non_polygon_geometry",
        "outside_crs84_bounds",
        "invalid_geometry",
        "zero_area",
    }
    assert set(points["point_polygon_status_counts"]) == {
        "eligible",
        "conflicting_labels",
        "no_valid_label",
        "unobserved",
    }
    assert points["point_polygon_status_counts"] == {
        "eligible": points["eligible_polygon_count"],
        "conflicting_labels": points["conflict_polygon_count"],
        "no_valid_label": points["no_valid_label_polygon_count"],
        "unobserved": points["unobserved_valid_polygon_count"],
    }

    for cohort in report["cohorts"].values():
        assert set(cohort) == {
            "source_name",
            "source_id",
            "stream",
            "partition_results",
            "point_eligible_sentence_instances_by_status",
            "repeated_partition_sentence_rows_coalesced",
            "sample",
            "selected",
            "selected_manifest",
        }
        assert set(cohort["sample"]) == {
            "source",
            "seed",
            "target_per_label",
            "cell_size_metres",
            "max_per_cell",
            "eligible_by_label",
            "selected_by_label",
            "shortfall_by_label",
            "unique_eligible_polygons",
            "unique_selected_polygons",
            "occupied_cells",
            "selected_cells",
            "repeated_source_rows_skipped",
            "multi_polygon_sentences_anchored",
            "non_binary_statuses",
            "selected_sentence_ids_by_polygon",
            "selected",
        }
        assert set(cohort["selected_manifest"]) == {"key_fields", "entries"}
        assert cohort["selected_manifest"]["entries"] == cohort["selected"]
        assert set(cohort["partition_results"][0]) == {
            "region",
            "stream",
            "text_unit_rows_scanned",
            "duplicate_validation_rows",
            "source_label_rows",
            "joined_to_point_eligible_polygons",
            "not_joined_to_point_eligible_polygons",
            "point_eligible_sentence_instances",
        }
        for selected in cohort["selected"]:
            assert set(selected) == {
                "sentence_id",
                "relevance",
                "anchor_polygon_id",
                "cell",
                "natural_key",
                "origin_region",
            }


def test_point_result_preserves_decision_digest_and_status_contract() -> None:
    decision = PointPolygonDecision(
        polygon_id="osm:way/1",
        status=PointPolygonStatus.ELIGIBLE,
        label="C00",
        located_observations=2,
        valid_observations=2,
        missing_label_observations=0,
        invalid_label_observations=0,
        unmapped_label_observations=0,
        observation_mode_counts={"actual_gps": 2},
        observed_labels=("C00",),
        anchor_longitude=12.5,
        anchor_latitude=45.5,
    )
    census = PointCensus(
        taxonomy_level="main_category",
        taxonomy_mapping_sha256="a" * 64,
        source_polygon_count=2,
        observation_count=2,
        duplicate_records_skipped=0,
        quality_valid_observations=2,
        located_observations=2,
        valid_labeled_observations=2,
        eligible_polygon_count=1,
        conflict_polygon_count=0,
        no_valid_label_polygon_count=0,
        boundary_observations=0,
        outside_observations=0,
        multiple_match_observations=0,
        rejection_counts={"wrong_survey_year": 1},
        input_observation_modes={"actual_gps": 2},
        quality_valid_observation_modes={"actual_gps": 2},
        located_observation_modes={"actual_gps": 2},
        decisions=(decision,),
    )
    polygon_stats = {
        "source_polygon_records": 2,
        "unique_source_polygon_records": 2,
        "duplicate_polygon_records_coalesced": 0,
        "valid_unique_source_polygons": 1,
        "geometry_exclusions": {
            "invalid_wkb": 0,
            "invalid_geojson": 0,
            "non_polygon_geometry": 0,
            "outside_crs84_bounds": 0,
            "invalid_geometry": 0,
            "zero_area": 0,
        },
    }
    result = point_result(census, polygon_stats)
    expected_decision = {
        "polygon_id": "osm:way/1",
        "status": "eligible",
        "label": "C00",
        "located_observations": 2,
        "valid_observations": 2,
        "missing_label_observations": 0,
        "invalid_label_observations": 0,
        "unmapped_label_observations": 0,
        "observed_labels": ["C00"],
        "anchor": [12.5, 45.5],
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            [expected_decision],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert result["point_decision_sha256"] == expected_digest
    assert result["point_decision_sha256"] == point_decision_digest(census)
    assert set(result["point_polygon_status_counts"]) == {
        "eligible",
        "conflicting_labels",
        "no_valid_label",
        "unobserved",
    }
    assert result["point_polygon_status_counts"] == {
        "eligible": 1,
        "conflicting_labels": 0,
        "no_valid_label": 0,
        "unobserved": 0,
    }
    polygon_stats["valid_unique_source_polygons"] = 2
    assert point_result(census, polygon_stats)["unobserved_valid_polygon_count"] == 1


@pytest.mark.parametrize("source_name", ["Description", "Website", "Wikidata"])
def test_point_source_census_joins_pinned_keys_and_samples_only_unanimous_gps_polygons(
    repository_root: Path, tmp_path: Path, source_name: str
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = pool_census._load_config(config_path)
    source = config["sources"][source_name]
    input_root = _snapshot_root(
        tmp_path / "input",
        source["input_repository"],
        source["input_revision"],
    )
    label_root = _snapshot_root(
        tmp_path / "labels",
        source["label_repository"],
        source["label_revision"],
    )
    source_files = _source_and_label_files(source_name, source, input_root, label_root)
    gpkg_path = tmp_path / "lucas-fixture.gpkg"
    _write_point_gpkg(gpkg_path)
    lucas_source_config_path = tmp_path / "lucas-config.json"
    gpkg_sha256 = hashlib.sha256(gpkg_path.read_bytes()).hexdigest()
    lucas_source_config_path.write_text(
        json.dumps(
            {
                "size_bytes": gpkg_path.stat().st_size,
                "sha256": gpkg_sha256,
            }
        ),
        encoding="utf-8",
    )
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files
    }

    report = run_point_source_census(
        input_root,
        label_root,
        gpkg_path,
        config_path,
        lucas_source_config_path,
        source_name,
        regions=[REGION],
        target_per_label=2,
        checkpoint_dir=tmp_path / "checkpoints",
    )

    _assert_report_schema(report, source, source_name)
    points = report["point_census"]
    assert points["observation_count"] == 7
    assert points["eligible_polygon_count"] == 1
    assert points["conflict_polygon_count"] == 1
    assert points["boundary_observations"] == 1
    assert points["outside_observations"] == 1
    assert points["rejection_counts"]["wrong_survey_year"] == 1
    eligible_polygon_id = "osm:way/1" if source_name == "Description" else "poly-a"
    assert points["eligible_polygon_labels"] == {eligible_polygon_id: "C00"}
    assert points["valid_labeled_observations"] == 4

    if source_name == "Wikidata":
        assert set(report["cohorts"]) == {"wikipedia", "wikivoyage"}
        wiki = report["cohorts"]["wikipedia"]["sample"]
        voyage = report["cohorts"]["wikivoyage"]["sample"]
        assert wiki["eligible_by_label"] == {"yes": 1, "no": 0}
        assert voyage["eligible_by_label"] == {"yes": 0, "no": 1}
        selected_ids = {
            report["cohorts"][name]["selected"][0]["sentence_id"]
            for name in ("wikipedia", "wikivoyage")
        }
        assert len(selected_ids) == 2
    else:
        cohort = report["cohorts"][source["streams"][0]["name"]]
        assert cohort["sample"]["eligible_by_label"] == {"yes": 1, "no": 1}
        assert cohort["sample"]["selected_by_label"] == {"yes": 1, "no": 1}
        assert cohort["sample"]["shortfall_by_label"] == {"yes": 1, "no": 1}

    serialized = json.dumps(report, sort_keys=True)
    assert "private fixture" not in serialized
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == before[path]
        for path in source_files
    )


def test_point_source_checkpoints_resume_without_changing_selected_manifest(
    repository_root: Path, tmp_path: Path
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = pool_census._load_config(config_path)
    source = config["sources"]["Description"]
    input_root = _snapshot_root(
        tmp_path / "input",
        source["input_repository"],
        source["input_revision"],
    )
    label_root = _snapshot_root(
        tmp_path / "labels",
        source["label_repository"],
        source["label_revision"],
    )
    _source_and_label_files("Description", source, input_root, label_root)
    gpkg_path = tmp_path / "lucas-fixture.gpkg"
    _write_point_gpkg(gpkg_path)
    lucas_source_config_path = tmp_path / "lucas-config.json"
    lucas_source_config_path.write_text(
        json.dumps(
            {
                "size_bytes": gpkg_path.stat().st_size,
                "sha256": hashlib.sha256(gpkg_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    def run():
        return run_point_source_census(
            input_snapshot=input_root,
            label_snapshot=label_root,
            gpkg_path=gpkg_path,
            config_path=config_path,
            lucas_source_config_path=lucas_source_config_path,
            source_name="Description",
            regions=[REGION],
            checkpoint_dir=tmp_path / "checkpoints",
        )

    first = run()
    checkpoint_paths = sorted((tmp_path / "checkpoints").rglob("*.json"))
    assert len(checkpoint_paths) == 3
    checkpoints = [
        json.loads(path.read_text(encoding="utf-8")) for path in checkpoint_paths
    ]
    base_identity_keys = {
        "source_id",
        "source_name",
        "input_repository",
        "input_revision",
        "label_repository",
        "label_revision",
        "geometry_encoding",
        "lucas_sha256",
        "config_hashes",
        "code_identity",
        "scope",
        "taxonomy_level",
        "sampling",
    }
    for checkpoint in checkpoints:
        expected_checkpoint_keys = {
            "checkpoint_schema",
            "identity",
            "result",
        }
        if checkpoint["checkpoint_schema"] != "lucas-asset-verification/v1":
            expected_checkpoint_keys.add("result_sha256")
        assert set(checkpoint) == expected_checkpoint_keys
    asset_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint["checkpoint_schema"] == "lucas-asset-verification/v1"
    )
    assert set(asset_checkpoint["identity"]) == {
        "asset_path",
        "asset_signature",
        "code_identity",
        "expected_sha256",
        "expected_size_bytes",
        "lucas_config_sha256",
        "stage",
    }
    assert set(asset_checkpoint["result"]) == {"sha256", "size_bytes"}
    point_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint["identity"].get("stage") == "point-census"
    )
    candidate_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint["identity"].get("stage") == "candidate-join"
    )
    for checkpoint in (point_checkpoint, candidate_checkpoint):
        identity = checkpoint["identity"]
        assert identity["source_name"] == "Description"
        assert identity["source_id"] == source["source_id"]
        assert identity["input_repository"] == source["input_repository"]
        assert identity["input_revision"] == source["input_revision"]
        assert identity["label_repository"] == source["label_repository"]
        assert identity["label_revision"] == source["label_revision"]
        assert identity["taxonomy_level"] == "main_category"
        assert identity["scope"] == {"regions": [REGION], "streams": ["description"]}
        assert identity["sampling"] == {
            "target_per_label": 500,
            "seed": 42,
            "cell_size_metres": 50_000,
            "max_per_cell": 20,
        }
    assert set(point_checkpoint["identity"]) == base_identity_keys | {
        "stage",
        "polygon_files",
    }
    assert set(candidate_checkpoint["identity"]) == base_identity_keys | {
        "stage",
        "region",
        "stream",
        "point_index_sha256",
        "input_files",
    }
    assert set(candidate_checkpoint["result"]) == {
        "region",
        "stream",
        "text_unit_rows_scanned",
        "duplicate_validation_rows",
        "source_label_rows",
        "joined_to_point_eligible_polygons",
        "not_joined_to_point_eligible_polygons",
        "point_eligible_sentence_instances",
        "candidates",
    }
    assert all(
        set(candidate)
        == {
            "sentence_id",
            "sentence_key",
            "status",
            "polygon_ids",
            "origin_region",
        }
        for candidate in candidate_checkpoint["result"]["candidates"]
    )
    for checkpoint in checkpoints:
        identity = checkpoint["identity"]
        identity_hash = hashlib.sha256(
            json.dumps(
                identity,
                **(
                    {"sort_keys": True, "separators": (",", ":")}
                    if checkpoint["checkpoint_schema"] == "lucas-asset-verification/v1"
                    else {
                        "ensure_ascii": False,
                        "sort_keys": True,
                        "separators": (",", ":"),
                    }
                ),
            ).encode("utf-8")
        ).hexdigest()
        stem = "-".join(
            str(identity[key])
            for key in ("source_id", "region", "stream")
            if identity.get(key) is not None
        ) or (
            "asset"
            if checkpoint["checkpoint_schema"] == "lucas-asset-verification/v1"
            else "run"
        )
        directory = {
            "lucas-asset-verification/v1": "assets",
            "lucas-point-census-checkpoint/v1": "point",
            "lucas-point-candidate-checkpoint/v1": "candidates",
        }[checkpoint["checkpoint_schema"]]
        path = next(
            path
            for path in checkpoint_paths
            if json.loads(path.read_text(encoding="utf-8")) == checkpoint
        )
        assert path.relative_to(tmp_path / "checkpoints") == Path(directory) / (
            f"{stem}-{identity_hash[:16]}.json"
        )
    resumed = run()

    assert resumed["cohorts"] == first["cohorts"]
    assert resumed["checkpointing"]["point_census_reused"] is True
    assert resumed["checkpointing"]["candidate_checkpoints_reused"] == 1


def test_point_source_rejects_geometry_change_between_census_stages(
    repository_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = pool_census._load_config(config_path)
    source = config["sources"]["Description"]
    input_root = _snapshot_root(
        tmp_path / "input",
        source["input_repository"],
        source["input_revision"],
    )
    label_root = _snapshot_root(
        tmp_path / "labels",
        source["label_repository"],
        source["label_revision"],
    )
    _source_and_label_files("Description", source, input_root, label_root)
    gpkg_path = tmp_path / "lucas-fixture.gpkg"
    _write_point_gpkg(gpkg_path)
    lucas_source_config_path = tmp_path / "lucas-config.json"
    lucas_source_config_path.write_text(
        json.dumps(
            {
                "size_bytes": gpkg_path.stat().st_size,
                "sha256": hashlib.sha256(gpkg_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    original_run_point_stage = point_runner._run_point_stage

    def change_geometry_after_point_stage(context, path):
        point_stage = original_run_point_stage(context, path)
        _write_parquet(
            input_root,
            f"data/{REGION}-latest.parquet",
            [
                {"osm_type": "way", "osm_id": 77, "geometry": POLYGON_A.wkb},
                {"osm_type": "way", "osm_id": 2, "geometry": POLYGON_B.wkb},
            ],
        )
        return point_stage

    monkeypatch.setattr(
        point_runner, "_run_point_stage", change_geometry_after_point_stage
    )

    with pytest.raises(
        ValueError, match="pinned source file changed during point census"
    ):
        run_point_source_census(
            input_snapshot=input_root,
            label_snapshot=label_root,
            gpkg_path=gpkg_path,
            config_path=config_path,
            lucas_source_config_path=lucas_source_config_path,
            source_name="Description",
            regions=[REGION],
            checkpoint_dir=tmp_path / "checkpoints",
        )


def test_checkpoint_payload_digest_rejects_tampered_result(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    identity = {"source_id": "description", "stage": "fixture"}
    result = {"eligible_polygon_count": 1}
    _write_checkpoint(path, "fixture/v1", identity, result)

    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint["result"]["eligible_polygon_count"] = 2
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(ValueError, match="payload digest mismatch"):
        _read_checkpoint(path, "fixture/v1", identity)


@pytest.mark.parametrize(
    ("checkpoint", "message"),
    [
        ([], "invalid point-census checkpoint"),
        (
            {"checkpoint_schema": "other/v1", "identity": {}, "result": {}},
            "unsupported point-census checkpoint",
        ),
        (
            {
                "checkpoint_schema": "fixture/v1",
                "identity": {"key": "other"},
                "result": {},
            },
            "identity mismatch",
        ),
        (
            {
                "checkpoint_schema": "fixture/v1",
                "identity": {"key": "expected"},
            },
            "result is invalid",
        ),
    ],
)
def test_checkpoint_header_rejects_invalid_payload_shapes(
    tmp_path: Path, checkpoint, message
):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _read_checkpoint(path, "fixture/v1", {"key": "expected"})


def test_checkpoint_write_is_idempotent_and_detects_conflicting_results(
    tmp_path: Path,
):
    path = tmp_path / "checkpoint.json"
    identity = {"source_id": "description"}
    result = {"eligible_polygon_count": 1}
    _write_checkpoint(path, "fixture/v1", identity, result)

    _write_checkpoint(path, "fixture/v1", identity, result)
    assert _read_checkpoint(path, "fixture/v1", identity) == result

    with pytest.raises(ValueError, match="conflicting point-census checkpoint"):
        _write_checkpoint(path, "fixture/v1", identity, {"eligible_polygon_count": 2})


def test_scope_and_sampling_options_validate_names_and_values(repository_root: Path):
    config = pool_census._load_config(
        repository_root / "config/lucas-source-pools.json"
    )
    source = config["sources"]["Wikidata"]
    assert _selected_regions(config, None) == config["scope"]["regions"]
    assert _selected_regions(config, [REGION]) == [REGION]
    assert _selected_stream_names(source, None) == [
        stream["name"] for stream in source["streams"]
    ]
    assert _selected_stream_names(source, ["wikipedia"]) == ["wikipedia"]
    _validate_target_per_label(1)
    _validate_seed(0)

    with pytest.raises(ValueError, match="non-empty and unique"):
        _selected_regions(config, [])
    with pytest.raises(ValueError, match="non-empty and unique"):
        _selected_regions(config, [REGION, REGION])
    with pytest.raises(ValueError, match="outside pinned scope"):
        _selected_regions(config, ["unknown-region"])
    with pytest.raises(ValueError, match="non-empty and unique"):
        _selected_stream_names(source, [])
    with pytest.raises(ValueError, match="non-empty and unique"):
        _selected_stream_names(source, ["wikipedia", "wikipedia"])
    with pytest.raises(ValueError, match="positive integer"):
        _validate_target_per_label(0)
    with pytest.raises(ValueError, match="positive integer"):
        _validate_target_per_label(True)
    with pytest.raises(ValueError, match="seed must be an integer"):
        _validate_seed(False)


def test_polygon_inventory_counts_exclusions_and_coalesces_only_matching_duplicates():
    inventory = _PolygonInventory()
    _record_polygon("polygon-a", POLYGON_A, None, "digest-a", inventory)
    _record_polygon("polygon-a", POLYGON_A, None, "digest-a", inventory)
    _record_polygon("polygon-b", None, "invalid_wkb", "digest-b", inventory)

    assert inventory.report()["duplicate_polygon_records_coalesced"] == 1
    assert inventory.report()["geometry_exclusions"]["invalid_wkb"] == 1
    assert [polygon.polygon_id for polygon in inventory.polygons()] == ["polygon-a"]

    with pytest.raises(ValueError, match="conflicting geometries"):
        _record_polygon("polygon-a", POLYGON_B, None, "different-digest", inventory)


def test_report_and_checkpoint_paths_cannot_overlap_source_cache(tmp_path: Path):
    cache = tmp_path / "hf-cache"

    with pytest.raises(ValueError, match="separate from protected path"):
        _validate_separate_path(cache / "report.json", [cache], "report output")

    with pytest.raises(ValueError, match="separate from protected path"):
        _validate_separate_path(
            tmp_path / "checkpoints" / "report.json",
            [tmp_path / "checkpoints"],
            "report output",
        )


def test_wiki_sentence_links_are_coalesced_across_regional_partitions(
    repository_root: Path,
):
    source = pool_census._load_config(
        repository_root / "config/lucas-source-pools.json"
    )["sources"]["Wikidata"]
    partition_results = [
        {
            "candidates": [
                {
                    "sentence_id": "wiki-sentence-1",
                    "status": "yes",
                    "sentence_key": ["wiki-sentence-1"],
                    "polygon_ids": ["osm:way/2"],
                    "origin_region": "austria",
                }
            ]
        },
        {
            "candidates": [
                {
                    "sentence_id": "wiki-sentence-1",
                    "status": "yes",
                    "sentence_key": ["wiki-sentence-1"],
                    "polygon_ids": ["osm:way/1"],
                    "origin_region": "albania",
                }
            ]
        },
    ]

    candidates, metadata, repeated_rows = _sampling_candidates(
        source, partition_results
    )
    assert candidates == [
        {
            "sentence_id": "wiki-sentence-1",
            "status": "yes",
            "polygon_ids": ["osm:way/1", "osm:way/2"],
        }
    ]
    assert metadata["wiki-sentence-1"]["origin_region"] == "albania"
    assert repeated_rows == 1

    sample = sample_point_sentence_instances(
        candidates,
        _PointIndex(
            {
                "osm:way/1": (12.5, 45.5),
                "osm:way/2": (14.5, 45.5),
            }
        ),
        source="wikidata/wikipedia",
        target_per_label=1,
    )
    assert sample.unique_eligible_polygons == 2
    assert sample.multi_polygon_sentences_anchored == 1
    assert sample.selected[0].anchor_polygon_id == "osm:way/1"


def test_wiki_duplicate_sentence_with_conflicting_labels_fails_closed(
    repository_root: Path,
):
    source = pool_census._load_config(
        repository_root / "config/lucas-source-pools.json"
    )["sources"]["Wikidata"]
    partition_results = [
        {
            "candidates": [
                {
                    "sentence_id": "wiki-sentence-1",
                    "status": status,
                    "sentence_key": ["wiki-sentence-1"],
                    "polygon_ids": [polygon_id],
                    "origin_region": region,
                }
            ]
        }
        for status, polygon_id, region in (
            ("yes", "osm:way/1", "albania"),
            ("no", "osm:way/2", "austria"),
        )
    ]

    with pytest.raises(ValueError, match="conflicting labels or keys"):
        _sampling_candidates(source, partition_results)


def test_sampling_options_are_validated_before_gpkg_or_config_reads(
    repository_root: Path, tmp_path: Path
):
    config_path = repository_root / "config/lucas-source-pools.json"
    source = pool_census._load_config(config_path)["sources"]["Description"]
    input_root = _snapshot_root(
        tmp_path / "input", source["input_repository"], source["input_revision"]
    )
    label_root = _snapshot_root(
        tmp_path / "labels", source["label_repository"], source["label_revision"]
    )

    with pytest.raises(ValueError, match="cell_size_metres must be a positive"):
        run_point_source_census(
            input_root,
            label_root,
            tmp_path / "missing.gpkg",
            config_path,
            tmp_path / "missing-lucas-config.json",
            "Description",
            regions=[REGION],
            cell_size_metres=0,
            checkpoint_dir=tmp_path / "checkpoints",
        )


def test_invalid_taxonomy_is_validated_before_gpkg_or_config_reads(
    repository_root: Path, tmp_path: Path
):
    config_path = repository_root / "config/lucas-source-pools.json"
    source = pool_census._load_config(config_path)["sources"]["Description"]
    input_root = _snapshot_root(
        tmp_path / "input", source["input_repository"], source["input_revision"]
    )
    label_root = _snapshot_root(
        tmp_path / "labels", source["label_repository"], source["label_revision"]
    )

    with pytest.raises(ValueError, match="unknown LUCAS C3 taxonomy level"):
        run_point_source_census(
            input_root,
            label_root,
            tmp_path / "missing.gpkg",
            config_path,
            tmp_path / "missing-lucas-config.json",
            "Description",
            regions=[REGION],
            taxonomy_level="not-a-taxonomy-level",
            checkpoint_dir=tmp_path / "checkpoints",
        )


@settings(max_examples=60, derandomize=True, deadline=None)
@given(
    link_groups=st.lists(
        st.lists(
            st.sampled_from(["osm:way/1", "osm:way/2", "osm:way/3", "osm:way/4"]),
            min_size=1,
            max_size=4,
            unique=True,
        ),
        min_size=1,
        max_size=8,
    )
)
def test_wiki_partition_coalescing_is_union_and_order_invariant(link_groups):
    partitions = [
        {
            "candidates": [
                {
                    "sentence_id": "stable-sentence",
                    "status": "yes",
                    "sentence_key": ["stable-sentence"],
                    "polygon_ids": links,
                    "origin_region": f"region-{index:02d}",
                }
            ]
        }
        for index, links in enumerate(link_groups)
    ]

    merged = coalesce_partition_candidates("wiki-document-sentences-v1", partitions)
    reversed_merged = coalesce_partition_candidates(
        "wiki-document-sentences-v1", list(reversed(partitions))
    )

    assert merged == reversed_merged
    assert merged[0][0]["polygon_ids"] == sorted(
        {polygon_id for links in link_groups for polygon_id in links}
    )
    assert merged[2] == len(link_groups) - 1


def test_cli_runs_explicit_snapshots_and_writes_a_new_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    captured = {}
    report = {
        "report_schema": "synthetic/v1",
        "scope_complete": False,
        "note": "é",
    }

    def fake_run(input_snapshot, label_snapshot, *args, **kwargs):
        captured["input_snapshot"] = input_snapshot
        captured["label_snapshot"] = label_snapshot
        captured["args"] = args
        captured["kwargs"] = kwargs
        return report

    monkeypatch.setattr(point_runner, "run_point_source_census", fake_run)
    input_snapshot = tmp_path / "input-snapshot"
    label_snapshot = tmp_path / "label-snapshot"
    gpkg_path = tmp_path / "lucas.gpkg"
    config_path = tmp_path / "source-config.json"
    lucas_source_config_path = tmp_path / "lucas-source-config.json"
    checkpoint_dir = tmp_path / "checkpoints"
    output = tmp_path / "reports" / "synthetic.json"

    point_runner.main(
        [
            "--source-snapshot",
            str(input_snapshot),
            "--label-snapshot",
            str(label_snapshot),
            "--lucas-gpkg",
            str(gpkg_path),
            "--config",
            str(config_path),
            "--lucas-source-config",
            str(lucas_source_config_path),
            "--source",
            "Description",
            "--region",
            "albania",
            "--region",
            "austria",
            "--stream",
            "description",
            "--taxonomy-level",
            "detailed",
            "--target-per-label",
            "3",
            "--seed",
            "7",
            "--cell-size-metres",
            "20000",
            "--max-per-cell",
            "4",
            "--checkpoint-dir",
            str(checkpoint_dir),
            "--output",
            str(output),
        ]
    )

    assert captured["input_snapshot"] == input_snapshot
    assert captured["label_snapshot"] == label_snapshot
    assert captured["args"] == (
        gpkg_path,
        config_path,
        lucas_source_config_path,
        "Description",
    )
    assert captured["kwargs"] == {
        "regions": ["albania", "austria"],
        "stream_names": ["description"],
        "taxonomy_level": "detailed",
        "target_per_label": 3,
        "seed": 7,
        "cell_size_metres": 20_000.0,
        "max_per_cell": 4,
        "checkpoint_dir": checkpoint_dir,
    }
    assert output.read_text(encoding="utf-8") == (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    assert json.loads(capsys.readouterr().out) == {
        "report": str(output),
        "report_schema": "synthetic/v1",
    }


def test_cli_parser_preserves_required_options_and_sampling_defaults(
    tmp_path: Path,
):
    parser = point_runner._build_parser()
    args = [
        "--source-snapshot",
        str(tmp_path / "input"),
        "--label-snapshot",
        str(tmp_path / "labels"),
        "--lucas-gpkg",
        str(tmp_path / "lucas.gpkg"),
        "--source",
        "Description",
        "--checkpoint-dir",
        str(tmp_path / "checkpoints"),
        "--output",
        str(tmp_path / "report.json"),
    ]
    parsed = parser.parse_args(args)
    assert parser.description == point_runner.__doc__
    assert parsed.hf_cache_root is None
    assert parsed.source_snapshot == tmp_path / "input"
    assert parsed.label_snapshot == tmp_path / "labels"
    assert parsed.lucas_gpkg == tmp_path / "lucas.gpkg"
    assert parsed.config == pool_census._CONFIG_PATH
    assert parsed.lucas_source_config == pool_census._LUCAS_CONFIG_PATH
    assert parsed.source == "Description"
    assert parsed.regions is None
    assert parsed.streams is None
    assert parsed.taxonomy_level == "main_category"
    assert parsed.target_per_label == 500
    assert parsed.seed == 42
    assert parsed.cell_size_metres == 50_000
    assert parsed.max_per_cell == 20
    assert parsed.checkpoint_dir == tmp_path / "checkpoints"
    assert parsed.output == tmp_path / "report.json"

    for required_option in (
        "--lucas-gpkg",
        "--source",
        "--checkpoint-dir",
        "--output",
    ):
        missing_value = False
        reduced = []
        for token in args:
            if missing_value:
                missing_value = False
                continue
            if token == required_option:
                missing_value = True
                continue
            reduced.append(token)
        with pytest.raises(SystemExit):
            parser.parse_args(reduced)

    for source_name in ("Description", "Website", "Wikidata", "all"):
        source_args = list(args)
        source_args[source_args.index("Description")] = source_name
        assert parser.parse_args(source_args).source == source_name
    invalid_source_args = list(args)
    invalid_source_args[invalid_source_args.index("Description")] = "website"
    with pytest.raises(SystemExit):
        parser.parse_args(invalid_source_args)


def test_cli_refuses_to_overwrite_an_existing_report(tmp_path: Path):
    output = tmp_path / "existing.json"
    output.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        point_runner.main(
            [
                "--lucas-gpkg",
                str(tmp_path / "lucas.gpkg"),
                "--source",
                "Description",
                "--checkpoint-dir",
                str(tmp_path / "checkpoints"),
                "--output",
                str(output),
            ]
        )
    assert output.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "source_args",
    [
        [],
        ["--hf-cache-root", "cache", "--source-snapshot", "input"],
    ],
)
def test_single_source_cli_requires_one_complete_snapshot_mode(
    tmp_path: Path, source_args: list[str]
):
    with pytest.raises(SystemExit):
        point_runner.main(
            [
                "--lucas-gpkg",
                str(tmp_path / "lucas.gpkg"),
                "--source",
                "Description",
                "--checkpoint-dir",
                str(tmp_path / "checkpoints"),
                "--output",
                str(tmp_path / "report.json"),
                *source_args,
            ]
        )


def test_single_source_cli_resolves_pinned_cache_snapshots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    source = {
        "input_repository": "owner/input",
        "input_revision": "input-rev",
        "label_repository": "owner/labels",
        "label_revision": "label-rev",
    }
    monkeypatch.setattr(pool_census, "_load_config", lambda _path: {})
    monkeypatch.setattr(pool_census, "_source_config", lambda _config, _name: source)
    captured = {}

    def fake_run(input_snapshot, label_snapshot, *args, **kwargs):
        captured["input"] = input_snapshot
        captured["labels"] = label_snapshot
        return {"report_schema": "synthetic-cache/v1"}

    monkeypatch.setattr(point_runner, "run_point_source_census", fake_run)
    cache_root = tmp_path / "hf-cache"
    point_runner.main(
        [
            "--hf-cache-root",
            str(cache_root),
            "--lucas-gpkg",
            str(tmp_path / "lucas.gpkg"),
            "--source",
            "Description",
            "--checkpoint-dir",
            str(tmp_path / "checkpoints"),
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert captured["input"] == point_runner._snapshot_under_cache_root(
        cache_root, "owner/input", "input-rev"
    )
    assert captured["labels"] == point_runner._snapshot_under_cache_root(
        cache_root, "owner/labels", "label-rev"
    )


def test_cli_reports_a_race_when_an_output_file_appears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    def collide(_path, _report):
        raise FileExistsError

    monkeypatch.setattr(point_runner, "_write_new_json", collide)
    parser = point_runner._build_parser()

    with pytest.raises(SystemExit):
        point_runner._write_cli_report(
            tmp_path / "report.json", {"report_schema": "synthetic/v1"}, parser
        )


@pytest.mark.parametrize(
    "extra_args",
    [
        [],
        ["--source-snapshot", "input", "--label-snapshot", "labels"],
        ["--stream", "wikipedia"],
    ],
)
def test_all_source_cli_requires_cache_root_and_rejects_single_source_options(
    tmp_path: Path, extra_args: list[str]
):
    args = [
        "--lucas-gpkg",
        str(tmp_path / "lucas.gpkg"),
        "--source",
        "all",
        "--checkpoint-dir",
        str(tmp_path / "checkpoints"),
        "--output",
        str(tmp_path / "report.json"),
        *extra_args,
    ]
    if "--stream" in extra_args or "--source-snapshot" in extra_args:
        args.extend(["--hf-cache-root", str(tmp_path / "cache")])

    with pytest.raises(SystemExit):
        point_runner.main(args)


def test_all_source_cli_dispatches_and_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    captured = {}

    def fake_run(
        cache_root, gpkg_path, config_path, lucas_source_config_path, **kwargs
    ):
        captured["cache_root"] = cache_root
        captured["gpkg_path"] = gpkg_path
        captured["kwargs"] = kwargs
        return {"report_schema": "synthetic-all/v1", "scope_complete": True}

    monkeypatch.setattr(point_runner, "run_all_point_source_censuses", fake_run)
    cache_root = tmp_path / "hf-cache"
    output = tmp_path / "reports" / "all.json"
    point_runner.main(
        [
            "--hf-cache-root",
            str(cache_root),
            "--lucas-gpkg",
            str(tmp_path / "lucas.gpkg"),
            "--source",
            "all",
            "--checkpoint-dir",
            str(tmp_path / "checkpoints"),
            "--output",
            str(output),
        ]
    )

    assert captured["cache_root"] == cache_root
    assert captured["kwargs"]["checkpoint_dir"] == tmp_path / "checkpoints"
    assert json.loads(output.read_text(encoding="utf-8"))["scope_complete"] is True
    assert json.loads(capsys.readouterr().out)["report_schema"] == "synthetic-all/v1"


@pytest.mark.parametrize("scope_complete", [True, False])
def test_all_source_runner_uses_each_configured_pin_and_combines_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scope_complete: bool,
):
    config = {
        "sources": {
            name: {
                "input_repository": f"owner/{name}-input",
                "input_revision": f"input-{name}",
                "label_repository": f"owner/{name}-labels",
                "label_revision": f"labels-{name}",
            }
            for name in ("SourceA", "SourceB")
        }
    }
    monkeypatch.setattr(pool_census, "_load_config", lambda _path: config)
    calls = []

    def fake_run(input_snapshot, label_snapshot, *args, **kwargs):
        calls.append((input_snapshot, label_snapshot, args, kwargs))
        return {"scope": {"scope_complete": scope_complete}}

    monkeypatch.setattr(point_runner, "run_point_source_census", fake_run)
    report = run_all_point_source_censuses(
        tmp_path / "hf-cache",
        tmp_path / "lucas.gpkg",
        tmp_path / "source-config.json",
        tmp_path / "lucas-config.json",
        checkpoint_dir=tmp_path / "checkpoints",
    )

    assert len(calls) == 2
    assert calls[0][0] == point_runner._snapshot_under_cache_root(
        tmp_path / "hf-cache", "owner/SourceA-input", "input-SourceA"
    )
    assert calls[1][1] == point_runner._snapshot_under_cache_root(
        tmp_path / "hf-cache", "owner/SourceB-labels", "labels-SourceB"
    )
    assert report["scope_complete"] is scope_complete
    assert set(report["source_runs"]) == {"SourceA", "SourceB"}
