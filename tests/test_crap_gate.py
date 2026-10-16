import json

import pytest
from radon.complexity import cc_visit

from tools.check_crap import crap, main, measure, over_limit


def test_crap_uses_complexity_and_coverage() -> None:
    assert crap(5, 1.0) == 5.0
    assert crap(2, 0.0) == 6.0


def test_measure_counts_line_and_branch_coverage_per_function() -> None:
    coverage = {
        "files": {
            "src/example.py": {
                "functions": {
                    "example": {
                        "executed_lines": [10, 11],
                        "missing_lines": [12],
                        "executed_branches": [[10, 11]],
                        "missing_branches": [[10, 12]],
                    }
                }
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
                "closures": [],
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
                "functions": {
                    "Example.run": {
                        "executed_lines": [10],
                        "missing_lines": [],
                        "executed_branches": [],
                        "missing_branches": [],
                    }
                }
            }
        }
    }
    method = {
        "type": "method",
        "classname": "Example",
        "name": "run",
        "lineno": 10,
        "endline": 10,
        "complexity": 1,
        "closures": [],
    }
    radon = {
        "src/example.py": [
            {"type": "class", "name": "Example", "methods": [method]},
            method,
        ]
    }

    rows = measure(coverage, radon)

    assert len(rows) == 1
    assert rows[0]["name"] == "Example.run"


def test_nested_radon_closures_use_coverage_qualified_names() -> None:
    source = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    outer = cc_visit(source)[0]
    radon = {
        "src/example.py": [
            {
                "type": "function",
                "name": outer.name,
                "lineno": outer.lineno,
                "endline": outer.endline,
                "complexity": outer.complexity,
                "closures": [
                    {
                        "type": "function",
                        "name": inner.name,
                        "lineno": inner.lineno,
                        "endline": inner.endline,
                        "complexity": inner.complexity,
                        "closures": [],
                    }
                    for inner in outer.closures
                ],
            }
        ]
    }
    coverage = {
        "files": {
            "src/example.py": {
                "functions": {
                    "outer": {
                        "executed_lines": [1, 4],
                        "missing_lines": [],
                        "executed_branches": [],
                        "missing_branches": [],
                    },
                    "outer.inner": {
                        "executed_lines": [],
                        "missing_lines": [2, 3],
                        "executed_branches": [],
                        "missing_branches": [],
                    },
                }
            }
        }
    }

    rows = measure(coverage, radon)

    assert {row["name"] for row in rows} == {"outer", "outer.inner"}
    inner_row = next(row for row in rows if row["name"] == "outer.inner")
    assert inner_row["line_coverage"] == 0.0
    assert inner_row["crap_line"] == 2.0


def test_measure_fails_when_coverage_omits_source_file() -> None:
    with pytest.raises(ValueError, match="missing Radon source file"):
        measure({"files": {}}, {"src/example.py": []})


def test_measure_fails_when_coverage_omits_function() -> None:
    radon = {
        "src/example.py": [
            {
                "type": "function",
                "name": "unmeasured",
                "lineno": 10,
                "endline": 11,
                "complexity": 1,
                "closures": [],
            }
        ]
    }

    with pytest.raises(ValueError, match="missing function unmeasured"):
        measure({"files": {"src/example.py": {"functions": {}}}}, radon)


def test_measure_fails_when_function_has_no_line_coverage_data() -> None:
    coverage = {
        "files": {
            "src/example.py": {
                "functions": {
                    "unmeasured": {
                        "executed_lines": [],
                        "missing_lines": [],
                        "executed_branches": [],
                        "missing_branches": [],
                    }
                }
            }
        }
    }
    radon = {
        "src/example.py": [
            {
                "type": "function",
                "name": "unmeasured",
                "lineno": 10,
                "endline": 11,
                "complexity": 1,
                "closures": [],
            }
        ]
    }

    with pytest.raises(ValueError, match="no executable lines"):
        measure(coverage, radon)


def test_measure_fails_when_branch_coverage_data_is_missing() -> None:
    coverage = {
        "files": {
            "src/example.py": {
                "functions": {
                    "unmeasured": {"executed_lines": [10], "missing_lines": []}
                }
            }
        }
    }
    radon = {
        "src/example.py": [
            {
                "type": "function",
                "name": "unmeasured",
                "lineno": 10,
                "endline": 10,
                "complexity": 1,
                "closures": [],
            }
        ]
    }

    with pytest.raises(KeyError, match="executed_branches"):
        measure(coverage, radon)


def test_crap_gate_uses_a_strict_six_point_limit() -> None:
    below = {"crap_line": 5.999, "crap_branch": 5.5}
    at_limit = {"crap_line": 6.0, "crap_branch": 5.0}

    assert over_limit([below]) == []
    assert over_limit([at_limit]) == [at_limit]


def test_main_returns_failure_for_crap_at_limit(tmp_path, capsys) -> None:
    coverage_path = tmp_path / "coverage.json"
    radon_path = tmp_path / "radon.json"
    coverage_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/example.py": {
                        "functions": {
                            "uncovered": {
                                "executed_lines": [],
                                "missing_lines": [10, 11],
                                "executed_branches": [],
                                "missing_branches": [],
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    radon_path.write_text(
        json.dumps(
            {
                "src/example.py": [
                    {
                        "type": "function",
                        "name": "uncovered",
                        "lineno": 10,
                        "endline": 11,
                        "complexity": 2,
                        "closures": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = main([str(coverage_path), str(radon_path), "--max-crap", "6"])

    assert result == 1
    assert "FAIL src/example.py:10 uncovered" in capsys.readouterr().out


def test_main_rejects_non_finite_limit(tmp_path) -> None:
    coverage_path = tmp_path / "coverage.json"
    radon_path = tmp_path / "radon.json"
    coverage_path.write_text("{}", encoding="utf-8")
    radon_path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main([str(coverage_path), str(radon_path), "--max-crap", "nan"])

    assert error.value.code == 2
