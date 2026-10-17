import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
import shapely
from shapely.geometry import Polygon, box

from tools import lucas_direct_source_snapshot as direct_snapshot
from tools import lucas_source_pool_census as census
from tools.lucas_direct_source_snapshot import (
    _description_text_units,
    _direct_labels,
    _project_geometry,
    _validate_checkpoint_directory,
    _validate_snapshot_root,
    _website_text_units,
    _wiki_document_links,
    run_direct_source_pool_census,
)


def _snapshot_root(base, repository, revision):
    root = base / f"datasets--{repository.replace('/', '--')}" / "snapshots" / revision
    root.mkdir(parents=True)
    return root


def _write_parquet(root, relative, rows):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _source_files(source_name, source, input_root, label_root):
    geometry = box(0, 0, 1, 1)
    geojson = shapely.to_geojson(geometry)
    region = "albania"
    if source_name == "Description":
        _write_parquet(
            input_root,
            f"data/{region}-latest.parquet",
            [{"osm_type": "way", "osm_id": 7, "geometry": geometry.wkb}],
        )
        _write_parquet(
            input_root,
            f"language-v1/data/{region}-latest.parquet",
            [
                {
                    "description_identity": "desc-7",
                    "osm_type": "way",
                    "osm_id": 7,
                    "tag_key": "description",
                    "sentence_count": 1,
                    "sentences": ["fixture text is never read"],
                }
            ],
        )
        labels_path = f"labels/language-v1/data/{region}-latest.parquet"
        label = {
            "description_identity": "desc-7",
            "tag_key": "description",
            "sentence_index": 0,
        }
        labels = [label]
    elif source_name == "Website":
        _write_parquet(
            input_root,
            f"polygons/{region}-latest.parquet",
            [
                {
                    "polygon_id": "poly-7",
                    "geometry": geojson,
                    "website_sentence_count": 1,
                    "contact_website_sentence_count": 1,
                    "website_sentences": ["fixture text is never read"],
                }
            ],
        )
        labels_path = f"labels/polygons/{region}-latest.parquet"
        labels = [
            {"polygon_id": "poly-7", "field": field, "sentence_index": 0}
            for field in ("website", "contact_website")
        ]
    else:
        _write_parquet(
            input_root,
            f"polygons/{region}-latest.parquet",
            [{"polygon_id": "poly-7", "geometry": geojson}],
        )
        _write_parquet(
            input_root,
            f"polygon_document_links/{region}-latest.parquet",
            [{"document_id": "doc-7", "polygon_id": "poly-7"}],
        )
        _write_parquet(
            input_root,
            f"wikipedia/sentences/{region}-latest.parquet",
            [
                {
                    "sentence_id": "sentence-7",
                    "document_id": "doc-7",
                    "text": "fixture text is never read",
                }
            ],
        )
        labels_path = f"labels/wikipedia/sentences/{region}-latest.parquet"
        labels = [{"sentence_id": "sentence-7"}]
    _write_parquet(
        label_root,
        labels_path,
        [
            {
                **label,
                "decision": "yes",
                "input_revision": source["input_revision"],
            }
            for label in labels
        ],
    )


def test_input_fingerprint_detects_same_size_content_change(tmp_path):
    path = tmp_path / "snapshot.parquet"
    path.write_bytes(b"first")

    first, _ = direct_snapshot._input_file_fingerprint(path, include_content_hash=True)
    path.write_bytes(b"other")
    second, _ = direct_snapshot._input_file_fingerprint(path, include_content_hash=True)

    assert first["size_bytes"] == second["size_bytes"]
    assert first["sha256"] != second["sha256"]


def test_input_fingerprint_skips_content_hash_without_checkpoints(
    tmp_path, monkeypatch
):
    path = tmp_path / "snapshot.parquet"
    path.write_bytes(b"small fixture")
    monkeypatch.setattr(
        census,
        "_file_sha256",
        lambda _path: pytest.fail("unneeded content hash was computed"),
    )

    fingerprint, signature = direct_snapshot._input_file_fingerprint(
        path, include_content_hash=False
    )

    assert fingerprint == {"size_bytes": len(b"small fixture")}
    assert signature[2] == len(b"small fixture")


def test_input_fingerprint_rejects_change_while_hashing(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.parquet"
    path.write_bytes(b"first")
    original_sha256 = census._file_sha256
    original_signature = census._asset_signature
    signature_calls = 0

    def hash_then_change(input_path):
        digest = original_sha256(input_path)
        input_path.write_bytes(b"other")
        return digest

    def changed_signature(input_path):
        nonlocal signature_calls
        signature = original_signature(input_path)
        signature_calls += 1
        if signature_calls == 1:
            return signature
        return (*signature[:3], signature[3] + 1, signature[4] + 1)

    monkeypatch.setattr(census, "_file_sha256", hash_then_change)
    monkeypatch.setattr(census, "_asset_signature", changed_signature)

    with pytest.raises(ValueError, match="pinned snapshot file changed"):
        direct_snapshot._input_file_fingerprint(path, include_content_hash=True)


def test_stream_summary_does_not_apply_source_cohort_shortfall_target():
    summary = direct_snapshot._sum_direct_region_results([])

    assert summary["shortfall_scope_complete"] is False
    assert summary["shortfall_from_target"] is None


def test_snapshot_root_requires_exact_cache_repository_and_commit(tmp_path):
    expected = _snapshot_root(tmp_path, "owner/input", "a" * 40)
    assert _validate_snapshot_root(expected, "owner/input", "a" * 40) == expected

    with pytest.raises(ValueError, match="owner--input/snapshots"):
        _validate_snapshot_root(expected, "owner/input", "b" * 40)


def test_checkpoint_directory_must_not_overlap_source_or_label_snapshot(tmp_path):
    input_root = tmp_path / "input-cache" / "snapshots" / ("a" * 40)
    label_root = tmp_path / "label-cache" / "snapshots" / ("b" * 40)
    checkpoint_dir = input_root / "checkpoints"

    with pytest.raises(ValueError, match="separate from HF snapshots"):
        _validate_checkpoint_directory(checkpoint_dir, input_root, label_root)


def test_empty_polygon_is_classified_as_zero_area_before_bounds():
    geometry, exclusion = _project_geometry(Polygon().wkb, "wkb", object())

    assert geometry is None
    assert exclusion == "zero_area"


def test_direct_labels_normalize_unsplit_to_skipped(tmp_path):
    path = _write_parquet(
        tmp_path,
        "labels.parquet",
        [
            {
                "sentence_id": "s1",
                "decision": "skipped_unsplit",
                "input_revision": "a" * 40,
            }
        ],
    )

    (label,) = _direct_labels(path, ("sentence_id",))

    assert label["status"] == "skipped"


@pytest.mark.parametrize("source_name", ["Description", "Website", "Wikidata"])
def test_direct_snapshot_census_uses_source_keys_without_reading_text(
    repository_root, tmp_path, monkeypatch, source_name
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = census._load_config(config_path)
    source = config["sources"][source_name]
    input_root = _snapshot_root(
        tmp_path, source["input_repository"], source["input_revision"]
    )
    label_root = _snapshot_root(
        tmp_path, source["label_repository"], source["label_revision"]
    )
    _source_files(source_name, source, input_root, label_root)

    class IdentityTransformer:
        @staticmethod
        def transform(x, y):
            return x, y

    def census_polygons(geometries, _bounds, _gpkg_path, decisions):
        eligible = {polygon_id: "B00" for polygon_id, _ in geometries}
        return (
            eligible,
            Counter({"eligible": len(eligible)}),
            Counter(),
            0,
            0,
            decisions,
            None,
        )

    seen_columns = []
    read_rows = census._iter_parquet_rows
    read_text_units = direct_snapshot._direct_text_units

    def capture_columns(path, columns):
        seen_columns.append(set(columns))
        yield from read_rows(path, columns)

    monkeypatch.setattr(census, "_iter_parquet_rows", capture_columns)
    monkeypatch.setattr(census, "_projection", IdentityTransformer)
    monkeypatch.setattr(census, "_census_polygons", census_polygons)
    monkeypatch.setattr(
        census,
        "_asset_signature",
        lambda _path: (1, 1, len(b"fixture"), 1, 1),
    )
    asset_hash_calls = []

    def verify_asset(_path, _config):
        asset_hash_calls.append("verified")
        return hashlib.sha256(b"fixture").hexdigest()

    monkeypatch.setattr(census, "_verify_lucas_asset", verify_asset)
    monkeypatch.setattr(
        census, "_require_asset_signature", lambda _path, _signature: None
    )
    gpkg_path = tmp_path / "asset.gpkg"
    gpkg_path.write_bytes(b"fixture")
    lucas_config_path = tmp_path / "lucas.json"
    lucas_config_path.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(b"fixture").hexdigest(),
                "size_bytes": len(b"fixture"),
            }
        ),
        encoding="utf-8",
    )

    selected_streams = (
        ["description" if source_name == "Description" else "website"]
        if source_name != "Wikidata"
        else ["wikipedia"]
    )
    checkpoint_dir = tmp_path / "checkpoints"
    report = run_direct_source_pool_census(
        input_root,
        label_root,
        gpkg_path,
        config_path,
        lucas_config_path,
        source_name,
        regions=["albania"],
        stream_names=selected_streams,
        checkpoint_dir=checkpoint_dir,
    )

    assert report["input_mode"] == "local-pinned-hf-cache-snapshots"
    assert report["report_schema"] == "lucas-source-pool-census/v4"
    assert report["source_pins"]["input_revision"] == source["input_revision"]
    assert report["checkpointing"]["geometry_checkpoints_saved"] == 1
    assert report["checkpointing"]["region_results_saved"] == 1
    assert report["checkpointing"]["lucas_asset_hash_reused"] is False
    geometry_checkpoint = next((checkpoint_dir / "geometry").glob("*.json"))
    identity = json.loads(geometry_checkpoint.read_text(encoding="utf-8"))["identity"]
    polygon_path = input_root / source["direct_input"]["polygon_path_template"].format(
        region="albania", stream=selected_streams[0]
    )
    assert (
        identity["polygon_sha256"]
        == hashlib.sha256(polygon_path.read_bytes()).hexdigest()
    )
    result_checkpoint = next((checkpoint_dir / "results").glob("*.json"))
    result_identity = json.loads(result_checkpoint.read_text(encoding="utf-8"))[
        "identity"
    ]
    stream = census._stream_config(source, selected_streams[0])
    label_path = label_root / stream["labels_path_template"].format(
        region="albania", stream=selected_streams[0]
    )
    assert (
        result_identity["label_sha256"]
        == hashlib.sha256(label_path.read_bytes()).hexdigest()
    )
    assert all(
        entry["sha256"]
        == hashlib.sha256((input_root / entry["name"]).read_bytes()).hexdigest()
        for entry in result_identity["required_text_files"]
    )
    assert set(identity["code_identity"]) == {
        "direct_source_tool_sha256",
        "source_pool_census_tool_sha256",
        "description_census_tool_sha256",
        "package_init_sha256",
        "coverage_api_sha256",
        "runtime_versions",
    }
    assert set(identity["code_identity"]["coverage_api_sha256"]) == {
        "lucas_gpkg",
        "lucas_taxonomy",
        "polygon_coverage",
        "support_eligibility",
    }
    assert set(identity["code_identity"]["runtime_versions"]) == {
        "pyarrow",
        "pyproj",
        "proj",
        "proj_data",
        "epsg_database",
        "shapely",
        "geos",
    }
    assert report["aggregate_across_selected_streams"][
        "eligible_source_relevance_instances"
    ] == {"yes": 2 if source_name == "Website" else 1, "no": 0}
    stream_summary = report["stream_summaries"][selected_streams[0]]
    assert stream_summary["shortfall_scope_complete"] is False
    assert stream_summary["shortfall_from_target"] is None
    assert not any(
        {"sentences", "sentence", "text", "original_text"}.intersection(columns)
        for columns in seen_columns
    )

    monkeypatch.setattr(
        census,
        "_census_polygons",
        lambda *_args: pytest.fail("completed geometry checkpoint was not reused"),
    )
    monkeypatch.setattr(
        direct_snapshot,
        "_direct_text_units",
        lambda *_args: pytest.fail("completed region result was not reused"),
    )
    resumed = run_direct_source_pool_census(
        input_root,
        label_root,
        gpkg_path,
        config_path,
        lucas_config_path,
        source_name,
        regions=["albania"],
        stream_names=selected_streams,
        checkpoint_dir=checkpoint_dir,
    )
    assert resumed["checkpointing"]["geometry_checkpoints_reused"] == 1
    assert resumed["checkpointing"]["region_results_reused"] == 1
    assert resumed["checkpointing"]["lucas_asset_hash_reused"] is True
    assert len(asset_hash_calls) == 1
    assert resumed["stream_summaries"] == report["stream_summaries"]

    monkeypatch.setattr(census, "_census_polygons", census_polygons)
    monkeypatch.setattr(direct_snapshot, "_direct_text_units", read_text_units)
    original_file_sha256 = census._file_sha256
    package_init_path = (
        Path(direct_snapshot.__file__).resolve().parents[1]
        / "src/osm_polygon_lucas/__init__.py"
    )
    changed_package_init_sha = hashlib.sha256(b"changed package exports").hexdigest()

    def changed_package_init_sha256(path):
        if Path(path).resolve() == package_init_path.resolve():
            return changed_package_init_sha
        return original_file_sha256(path)

    monkeypatch.setattr(census, "_file_sha256", changed_package_init_sha256)
    rerun = run_direct_source_pool_census(
        input_root,
        label_root,
        gpkg_path,
        config_path,
        lucas_config_path,
        source_name,
        regions=["albania"],
        stream_names=selected_streams,
        checkpoint_dir=checkpoint_dir,
    )
    assert rerun["package_init_sha256"] == changed_package_init_sha
    assert rerun["checkpointing"]["lucas_asset_hash_reused"] is False
    assert rerun["checkpointing"]["geometry_checkpoints_reused"] == 0
    assert rerun["checkpointing"]["region_results_reused"] == 0
    assert len(asset_hash_calls) == 2


def test_spatial_screen_precedes_sentence_join_and_skips_zero_pool_text(
    repository_root, tmp_path, monkeypatch
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = census._load_config(config_path)
    source = config["sources"]["Website"]
    input_root = _snapshot_root(
        tmp_path, source["input_repository"], source["input_revision"]
    )
    label_root = _snapshot_root(
        tmp_path, source["label_repository"], source["label_revision"]
    )
    _source_files("Website", source, input_root, label_root)
    _write_parquet(
        input_root,
        "polygons/andorra-latest.parquet",
        [
            {
                "polygon_id": "poly-8",
                "geometry": shapely.to_geojson(box(0, 0, 1, 1)),
                "website_sentence_count": 1,
                "contact_website_sentence_count": 0,
            }
        ],
    )
    _write_parquet(
        label_root,
        "labels/polygons/andorra-latest.parquet",
        [
            {
                "polygon_id": "poly-8",
                "field": "website",
                "sentence_index": 0,
                "decision": "yes",
                "input_revision": source["input_revision"],
            }
        ],
    )

    class IdentityTransformer:
        @staticmethod
        def transform(x, y):
            return x, y

    events = []

    def census_polygons(geometries, _bounds, _gpkg_path, decisions):
        polygon_id = geometries[0][0]
        events.append(("geometry", polygon_id))
        if polygon_id == "poly-7":
            return (
                {},
                Counter({"below_threshold": 1}),
                Counter(),
                1,
                1,
                decisions,
                None,
            )
        return (
            {polygon_id: "B00"},
            Counter({"eligible": 1}),
            Counter(),
            1,
            1,
            decisions,
            None,
        )

    read_text_units = direct_snapshot._direct_text_units

    def capture_text_units(*args):
        region = args[3]
        events.append(("text", region))
        assert region == "andorra"
        return read_text_units(*args)

    monkeypatch.setattr(census, "_projection", IdentityTransformer)
    monkeypatch.setattr(census, "_census_polygons", census_polygons)
    monkeypatch.setattr(
        census,
        "_asset_signature",
        lambda _path: (1, 1, len(b"fixture"), 1, 1),
    )
    monkeypatch.setattr(
        census,
        "_verify_lucas_asset",
        lambda _path, _config: hashlib.sha256(b"fixture").hexdigest(),
    )
    monkeypatch.setattr(
        census, "_require_asset_signature", lambda _path, _signature: None
    )
    monkeypatch.setattr(direct_snapshot, "_direct_text_units", capture_text_units)
    gpkg_path = tmp_path / "asset.gpkg"
    gpkg_path.write_bytes(b"fixture")
    lucas_config_path = tmp_path / "lucas.json"
    lucas_config_path.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(b"fixture").hexdigest(),
                "size_bytes": len(b"fixture"),
            }
        ),
        encoding="utf-8",
    )

    report = run_direct_source_pool_census(
        input_root,
        label_root,
        gpkg_path,
        config_path,
        lucas_config_path,
        "Website",
        regions=["albania", "andorra"],
        checkpoint_dir=tmp_path / "checkpoints",
    )

    assert events == [
        ("geometry", "poly-7"),
        ("geometry", "poly-8"),
        ("text", "andorra"),
    ]
    albania, andorra = report["stream_results"]["website"]
    assert albania["eligible_source_relevance_counts"][
        "unique_eligible_sentence_instances_by_source_relevance"
    ] == {"yes": 0, "no": 0}
    assert albania["source_label_rows"] == {"yes": 2}
    assert albania["input_receipt"]["text_units"]["read"] is False
    assert andorra["eligible_source_relevance_counts"][
        "unique_eligible_sentence_instances_by_source_relevance"
    ] == {"yes": 1, "no": 0}
    assert report["checkpointing"]["geometry_checkpoints_saved"] == 2
    assert report["checkpointing"]["region_results_saved"] == 2
    assert report["stream_summaries"]["website"]["shortfall_scope_complete"] is False
    assert report["stream_summaries"]["website"]["shortfall_from_target"] is None


def test_missing_join_input_does_not_become_zero_eligible_count(
    repository_root, tmp_path, monkeypatch
):
    config_path = repository_root / "config/lucas-source-pools.json"
    config = census._load_config(config_path)
    source = config["sources"]["Wikidata"]
    input_root = _snapshot_root(
        tmp_path, source["input_repository"], source["input_revision"]
    )
    label_root = _snapshot_root(
        tmp_path, source["label_repository"], source["label_revision"]
    )
    _source_files("Wikidata", source, input_root, label_root)
    (input_root / "wikipedia/sentences/albania-latest.parquet").unlink()

    class IdentityTransformer:
        @staticmethod
        def transform(x, y):
            return x, y

    def census_polygons(geometries, _bounds, _gpkg_path, decisions):
        return (
            {polygon_id: "B00" for polygon_id, _ in geometries},
            Counter({"eligible": len(geometries)}),
            Counter(),
            1,
            1,
            decisions,
            None,
        )

    monkeypatch.setattr(census, "_projection", IdentityTransformer)
    monkeypatch.setattr(census, "_census_polygons", census_polygons)
    monkeypatch.setattr(
        census,
        "_asset_signature",
        lambda _path: (1, 1, len(b"fixture"), 1, 1),
    )
    monkeypatch.setattr(
        census,
        "_verify_lucas_asset",
        lambda _path, _config: hashlib.sha256(b"fixture").hexdigest(),
    )
    monkeypatch.setattr(
        census, "_require_asset_signature", lambda _path, _signature: None
    )
    gpkg_path = tmp_path / "asset.gpkg"
    gpkg_path.write_bytes(b"fixture")
    lucas_config_path = tmp_path / "lucas.json"
    lucas_config_path.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(b"fixture").hexdigest(),
                "size_bytes": len(b"fixture"),
            }
        ),
        encoding="utf-8",
    )

    report = run_direct_source_pool_census(
        input_root,
        label_root,
        gpkg_path,
        config_path,
        lucas_config_path,
        "Wikidata",
        regions=["albania"],
        stream_names=["wikipedia"],
    )

    result = report["stream_results"]["wikipedia"][0]
    assert result["eligible_source_relevance_counts"] is None
    assert result["eligible_pool_count_complete"] is False
    assert result["source_label_rows"] == {"yes": 1}
    assert result["missing_inputs"] == ["wikipedia/sentences/albania-latest.parquet"]
    assert (
        report["stream_summaries"]["wikipedia"]["eligible_pool_counts_complete"]
        is False
    )
    assert report["stream_summaries"]["wikipedia"]["shortfall_from_target"] is None


@pytest.mark.parametrize(
    "columns,row,expected_message",
    [
        (
            ("polygon_id", "website_sentence_count"),
            {"polygon_id": "missing-polygon", "website_sentence_count": 1},
            "Website row references a missing geometry polygon",
        ),
        (
            ("description_identity", "osm_type", "osm_id", "tag_key", "sentence_count"),
            {
                "description_identity": "missing-polygon",
                "osm_type": "way",
                "osm_id": 99,
                "tag_key": "description",
                "sentence_count": 1,
            },
            "Description text row references a polygon missing from geometry data",
        ),
        (
            ("document_id", "polygon_id"),
            {"document_id": "doc-7", "polygon_id": "missing-polygon"},
            "Wiki document link references a missing geometry polygon",
        ),
    ],
)
def test_direct_source_references_missing_geometry_are_rejected(
    monkeypatch, columns, row, expected_message
):
    monkeypatch.setattr(
        census,
        "_iter_parquet_rows",
        lambda _path, _columns: iter([row]),
    )
    with pytest.raises(ValueError, match=expected_message):
        if "website_sentence_count" in columns:
            list(
                _website_text_units(
                    Path("unused"), set(), {"website": "website_sentence_count"}
                )
            )
        elif "osm_type" in columns:
            list(_description_text_units(Path("unused"), set()))
        else:
            _wiki_document_links(Path("unused"), set())


def test_website_null_sentence_count_means_optional_field_is_absent(monkeypatch):
    monkeypatch.setattr(
        census,
        "_iter_parquet_rows",
        lambda _path, _columns: iter(
            [
                {
                    "polygon_id": "poly-7",
                    "website_sentence_count": None,
                    "contact_website_sentence_count": 1,
                }
            ]
        ),
    )

    rows = list(
        _website_text_units(
            Path("unused"),
            {"poly-7"},
            {
                "website": "website_sentence_count",
                "contact_website": "contact_website_sentence_count",
            },
        )
    )

    assert rows == [
        {
            "polygon_id": "poly-7",
            "field": "contact_website",
            "sentence_index": 0,
            "polygon_ids": ["poly-7"],
        }
    ]
