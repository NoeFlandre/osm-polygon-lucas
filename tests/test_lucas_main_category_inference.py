import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "tools" / "lucas_main_category_inference.py"
LAUNCHER_PATH = ROOT / "scripts" / "run_lucas_main_category_grid5000.sh"
SPEC = importlib.util.spec_from_file_location(
    "lucas_main_category_inference_under_test", RUNNER_PATH
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _runtime_info() -> dict[str, object]:
    return {
        "oar_job_id": "123456",
        "site": "grenoble",
        "node": "vercors17-1",
        "uv": runner.UV_VERSION,
        "python": runner.PYTHON_VERSION,
        "torch": runner.TORCH_VERSION,
        "cuda": "13.0",
        "transformers": runner.TRANSFORMERS_VERSION,
        "huggingface_hub": runner.HUGGINGFACE_HUB_VERSION,
        "pyarrow": runner.PYARROW_VERSION,
        "gpu": "Synthetic GPU",
        "compute_capability": "8.9",
        "memory_bytes": 40 * 1024**3,
        "bf16_supported": True,
        "runtime_pyproject_sha256": runner.RUNTIME_PYPROJECT_SHA256,
        "runtime_lock_sha256": runner.RUNTIME_UV_LOCK_SHA256,
    }


def test_prompt_contains_only_sentence_as_instance_data():
    prompt = runner._render_prompt("Synthetic grass cover.")
    normalized_prompt = " ".join(prompt.split())
    assert prompt.endswith("Sentence:\nSynthetic grass cover.\n")
    assert "target_label" not in prompt
    assert "relevance_stratum" not in prompt
    assert "Do not judge whether it is relevant." in normalized_prompt


@pytest.mark.parametrize(
    ("raw", "truncated", "expected"),
    [
        (
            "reasoning</think>\nFinal LUCAS main_category: C00<|im_end|>",
            False,
            ("C00", "valid"),
        ),
        (
            "reasoning</think>\nFinal LUCAS main_category: C00",
            True,
            (None, "truncated"),
        ),
        (
            "reasoning</think>\nFinal LUCAS main_category: C00",
            False,
            (None, "missing_eos"),
        ),
        (
            "reasoning\nFinal LUCAS main_category: C00",
            False,
            (None, "thinking_not_closed"),
        ),
        (
            "reasoning</think>\n<|im_end|>",
            False,
            (None, "missing_final_answer"),
        ),
        (
            "reasoning</think>\nFinal LUCAS main_category: C00\nExtra<|im_end|>",
            False,
            (None, "extra_final_text"),
        ),
        (
            "reasoning</think>\nFinal LUCAS main_category: Z99<|im_end|>",
            False,
            (None, "invalid_final_code"),
        ),
        (
            "reasoning</think>\nC00<|im_end|>",
            False,
            (None, "missing_final_answer_marker"),
        ),
    ],
)
def test_parser_accepts_only_one_canonical_final_answer(raw, truncated, expected):
    assert (
        runner._parse_final(raw, truncated=truncated, eos_texts={"<|im_end|>"})
        == expected
    )


def test_manifest_loader_rejects_non_sha1_pins_and_accepts_exact40(
    monkeypatch, tmp_path
):
    source = {
        "source_id": "description",
        "input_revision": "a" * 40,
        "label_revision": "b" * 40,
        "join_key_fields": ["description_identity", "tag_key", "sentence_index"],
        "streams": [{"name": "description"}],
    }
    entry = {
        "sentence_id": "synthetic-1",
        "anchor_polygon_id": "synthetic-polygon-1",
        "origin_region": "AT",
        "origin_streams": ["description"],
        "relevance_stratum": "yes",
        "target_taxonomy_level": "main_category",
        "target_label": "A00",
        "natural_key": {
            "description_identity": "synthetic-description",
            "tag_key": "synthetic-tag",
            "sentence_index": 0,
        },
    }
    manifest_root = tmp_path / "manifests"
    manifest_root.mkdir()
    manifest_path = manifest_root / "description.json"
    manifest_path.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
    digest = runner.sha256_file(manifest_path)
    monkeypatch.setattr(
        runner,
        "COHORTS",
        {
            "description": {
                "manifest": "description.json",
                "source_config": "Description",
                "sha256": digest,
                "rows": 1,
                "strata": {"yes": 1},
            }
        },
    )
    config_path = tmp_path / "source-pools.json"
    config = {
        "schema_version": 1,
        "scope": {"regions": ["AT"]},
        "sources": {"Description": source},
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    loaded, _, _ = runner._load_manifests(manifest_root, config_path)
    assert loaded["description"]["entries"] == [entry]

    source["input_revision"] += "c"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="malformed pinned input_revision"):
        runner._load_manifests(manifest_root, config_path)


def test_description_resolver_uses_zero_based_sentence_index(monkeypatch, tmp_path):
    parquet_path = tmp_path / "description.parquet"
    pq.write_table(
        pa.table(
            {
                "description_identity": ["synthetic-description"],
                "tag_key": ["synthetic-tag"],
                "sentences": [
                    ["first synthetic sentence", "selected synthetic sentence"]
                ],
            }
        ),
        parquet_path,
    )
    monkeypatch.setattr(
        runner,
        "_download_source_file",
        lambda *_args: (parquet_path, "language-v1/data/AT-latest.parquet"),
    )
    entry = {
        "sentence_id": "synthetic-description-1",
        "origin_region": "AT",
        "natural_key": {
            "description_identity": "synthetic-description",
            "tag_key": "synthetic-tag",
            "sentence_index": 1,
        },
    }
    result = runner._resolve_description([entry], {}, tmp_path / "scratch")
    assert result == {"synthetic-description-1": "selected synthetic sentence"}


def test_website_and_wiki_resolvers_handle_synthetic_pinned_keys(monkeypatch, tmp_path):
    website_path = tmp_path / "website.parquet"
    pq.write_table(
        pa.table(
            {
                "polygon_id": ["synthetic-polygon"],
                "website": [["synthetic website sentence"]],
                "contact_website": [["synthetic contact sentence"]],
            }
        ),
        website_path,
    )
    wiki_path = tmp_path / "wiki.parquet"
    pq.write_table(
        pa.table(
            {
                "sentence_id": ["synthetic-wiki-id"],
                "sentence": ["synthetic wiki sentence"],
            }
        ),
        wiki_path,
    )
    monkeypatch.setattr(
        runner,
        "_download_source_file",
        lambda _source, stream, _region, _scratch: (
            (website_path, "polygons/AT-latest.parquet")
            if stream == "website"
            else (wiki_path, "wikipedia/sentences/AT-latest.parquet")
        ),
    )
    website_entry = {
        "sentence_id": "synthetic-website-id",
        "origin_region": "AT",
        "natural_key": {
            "polygon_id": "synthetic-polygon",
            "field": "contact_website",
            "sentence_index": 0,
        },
    }
    wiki_entry = {
        "sentence_id": "synthetic-wiki-record",
        "origin_region": "AT",
        "origin_streams": ["wikipedia"],
        "natural_key": {"sentence_id": "synthetic-wiki-id"},
    }
    assert runner._resolve_website([website_entry], {}, tmp_path) == {
        "synthetic-website-id": "synthetic contact sentence"
    }
    assert runner._resolve_wiki([wiki_entry], {}, tmp_path) == {
        "synthetic-wiki-record": "synthetic wiki sentence"
    }


def test_runtime_info_is_pinned_to_versions_hardware_and_runtime_files(
    monkeypatch, tmp_path
):
    runtime_project = tmp_path / "runtime"
    runtime_project.mkdir()
    pyproject = runtime_project / "pyproject.toml"
    lock = runtime_project / "uv.lock"
    pyproject.write_text("synthetic runtime project", encoding="utf-8")
    lock.write_text("synthetic runtime lock", encoding="utf-8")
    monkeypatch.setattr(
        runner, "RUNTIME_PYPROJECT_SHA256", runner.sha256_file(pyproject)
    )
    monkeypatch.setattr(runner, "RUNTIME_UV_LOCK_SHA256", runner.sha256_file(lock))
    info = _runtime_info()
    info["runtime_pyproject_sha256"] = runner.RUNTIME_PYPROJECT_SHA256
    info["runtime_lock_sha256"] = runner.RUNTIME_UV_LOCK_SHA256

    digest = runner._validate_runtime_info(info, runtime_project)
    assert len(digest) == 64

    second_job_info = {**info, "oar_job_id": "123457", "node": "vercors18-1"}
    runtime_info_root = tmp_path / "output" / "runtime-info"
    runtime_info_root.mkdir(parents=True)
    for item in (info, second_job_info):
        path = runtime_info_root / f"oar-{item['oar_job_id']}.json"
        path.write_text(json.dumps(item), encoding="utf-8")
    assert runner._load_runtime_hashes(tmp_path / "output", runtime_project) == {
        digest,
        runner._validate_runtime_info(second_job_info, runtime_project),
    }

    info["compute_capability"] = "7.5"
    with pytest.raises(ValueError, match="hardware"):
        runner._validate_runtime_info(info, runtime_project)


def test_checkpoint_binds_runtime_and_rejects_source_text(tmp_path):
    entry = {
        "sentence_id": "synthetic-id",
        "anchor_polygon_id": "synthetic-polygon",
        "origin_region": "AT",
        "origin_streams": ["description"],
        "relevance_stratum": "yes",
        "target_label": "A00",
    }
    record = {
        "run_id": runner.RUN_ID,
        "cohort": "description",
        "sentence_id": "synthetic-id",
        "anchor_polygon_id": "synthetic-polygon",
        "origin_region": "AT",
        "origin_streams": ["description"],
        "relevance_stratum": "yes",
        "target_code": "A00",
        "predicted_code": "A00",
        "parse_status": "valid",
        "correct": True,
        "forced_close_reasoning": False,
        "truncated": False,
        "generated_tokens": 8,
        "raw_completion_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "prompt_sha256": "c" * 64,
        "runtime_info_sha256": "d" * 64,
    }
    checkpoint = tmp_path / "batch.jsonl"
    checkpoint.write_text(json.dumps(record) + "\n", encoding="utf-8")
    validated = runner._validate_saved_batch(
        checkpoint, [entry], "description", "b" * 64, "c" * 64, {"d" * 64}
    )
    assert validated == [record]
    manifests = {"description": {"sha256": "b" * 64, "entries": [entry]}}
    runner._validate_final_records([record], manifests, "c" * 64, {"d" * 64})

    for field, value in (
        ("sentence", "synthetic source text"),
        ("prompt", "synthetic source text"),
        ("resolved_sentence", "synthetic source text"),
    ):
        unsafe_record = {**record, field: value}
        checkpoint.write_text(json.dumps(unsafe_record) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="privacy schema"):
            runner._validate_saved_batch(
                checkpoint,
                [entry],
                "description",
                "b" * 64,
                "c" * 64,
                {"d" * 64},
            )
        with pytest.raises(ValueError, match="privacy-safe schema"):
            runner._validate_final_records(
                [unsafe_record], manifests, "c" * 64, {"d" * 64}
            )

    unsafe_record = {**record, "origin_region": "synthetic source text"}
    checkpoint.write_text(json.dumps(unsafe_record) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="privacy schema"):
        runner._validate_saved_batch(
            checkpoint, [entry], "description", "b" * 64, "c" * 64, {"d" * 64}
        )


def test_grid5000_launcher_has_valid_shell_syntax():
    result = subprocess.run(
        ["bash", "-n", str(LAUNCHER_PATH)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_grid5000_launcher_refuses_to_run_without_oar():
    environment = os.environ.copy()
    environment.pop("OAR_JOB_ID", None)
    result = subprocess.run(
        ["bash", str(LAUNCHER_PATH)],
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 2
    assert "Refusing to run outside an active OAR allocation." in result.stderr
