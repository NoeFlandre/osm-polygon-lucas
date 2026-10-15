from tools.check_crap import crap, measure, over_limit


def test_crap_uses_complexity_and_coverage() -> None:
    assert crap(5, 1.0) == 5.0
    assert crap(2, 0.0) == 6.0


def test_measure_counts_line_and_branch_coverage_per_function() -> None:
    coverage = {
        "files": {
            "src/example.py": {
                "executed_lines": [10, 11],
                "missing_lines": [12],
                "executed_branches": [[10, 11]],
                "missing_branches": [[10, 12]],
            }
        }
    }
    radon = {
        "src/example.py": [
            {
                "type": "function",
                "name": "example",
                "lineno": 10,
                "endline": 12,
                "complexity": 2,
            }
        ]
    }

    (row,) = measure(coverage, radon)

    assert row["line_coverage"] == 2 / 3
    assert row["branch_coverage"] == 0.5
    assert row["crap_line"] > row["complexity"]
    assert row["crap_branch"] > row["complexity"]


def test_measure_deduplicates_radon_class_methods() -> None:
    coverage = {
        "files": {
            "src/example.py": {
                "executed_lines": [10],
                "missing_lines": [],
                "executed_branches": [],
                "missing_branches": [],
            }
        }
    }
    method = {
        "type": "method",
        "name": "run",
        "lineno": 10,
        "endline": 10,
        "complexity": 1,
    }
    radon = {
        "src/example.py": [
            {"type": "class", "methods": [method]},
            method,
        ]
    }

    rows = measure(coverage, radon)

    assert len(rows) == 1


def test_measure_fails_when_coverage_omits_source_file() -> None:
    try:
        measure({"files": {}}, {"src/example.py": []})
    except ValueError as error:
        assert "missing Radon source file" in str(error)
    else:
        raise AssertionError("missing coverage input must fail closed")


def test_crap_gate_uses_a_strict_six_point_limit() -> None:
    below = {"crap_line": 5.999, "crap_branch": 5.5}
    at_limit = {"crap_line": 6.0, "crap_branch": 5.0}

    assert over_limit([below]) == []
    assert over_limit([at_limit]) == [at_limit]
