"""Measure per-function CRAP scores and enforce the approved strict limit."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def crap(complexity: int, coverage: float) -> float:
    """Return the CRAP score for cyclomatic complexity and coverage ratio."""
    return complexity**2 * (1 - coverage) ** 3 + complexity


def _ratio(executed: int, missing: int) -> float:
    total = executed + missing
    return 1.0 if total == 0 else executed / total


def _in_range(line: int, block: dict[str, Any]) -> bool:
    return block["lineno"] <= line <= block["endline"]


def _function_blocks(blocks: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield function/method blocks once, including methods nested in classes."""
    seen: set[tuple[str, int, int]] = set()
    for block in blocks:
        candidates = block.get("methods", []) if block["type"] == "class" else [block]
        for candidate in candidates:
            if candidate["type"] not in {"function", "method"}:
                continue
            identity = (candidate["name"], candidate["lineno"], candidate["endline"])
            if identity not in seen:
                seen.add(identity)
                yield candidate


def _coverage_ratios(
    block: dict[str, Any], file_data: dict[str, Any]
) -> tuple[float, float]:
    line_coverage = _ratio(
        sum(_in_range(line, block) for line in file_data["executed_lines"]),
        sum(_in_range(line, block) for line in file_data["missing_lines"]),
    )
    branch_coverage = _ratio(
        sum(_in_range(arc[0], block) for arc in file_data.get("executed_branches", [])),
        sum(_in_range(arc[0], block) for arc in file_data.get("missing_branches", [])),
    )
    return line_coverage, branch_coverage


def over_limit(
    rows: list[dict[str, Any]], maximum: float = 6.0
) -> list[dict[str, Any]]:
    """Return rows where either line or branch CRAP is not strictly below maximum."""
    return [
        row
        for row in rows
        if row["crap_line"] >= maximum or row["crap_branch"] >= maximum
    ]


def measure(coverage: dict[str, Any], radon: dict[str, Any]) -> list[dict[str, Any]]:
    """Return CRAP measurements for all Radon functions in the source report."""
    coverage_by_path = {
        Path(path).resolve(): data for path, data in coverage.get("files", {}).items()
    }
    rows: list[dict[str, Any]] = []
    for path, blocks in radon.items():
        resolved_path = Path(path).resolve()
        try:
            file_data = coverage_by_path[resolved_path]
        except KeyError as exc:
            raise ValueError(
                f"Coverage report is missing Radon source file {path}"
            ) from exc
        for block in _function_blocks(blocks):
            line_coverage, branch_coverage = _coverage_ratios(block, file_data)
            complexity = block["complexity"]
            rows.append(
                {
                    "path": path,
                    "line": block["lineno"],
                    "name": block["name"],
                    "complexity": complexity,
                    "line_coverage": line_coverage,
                    "branch_coverage": branch_coverage,
                    "crap_line": crap(complexity, line_coverage),
                    "crap_branch": crap(complexity, branch_coverage),
                }
            )
    if not rows:
        raise ValueError("Radon report contains no production functions to measure")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("radon_json", type=Path)
    parser.add_argument("--max-crap", type=float, default=6.0)
    args = parser.parse_args()
    if args.max_crap <= 0:
        parser.error("--max-crap must be positive")

    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    radon = json.loads(args.radon_json.read_text(encoding="utf-8"))
    rows = measure(coverage, radon)
    max_line = max(row["crap_line"] for row in rows)
    max_branch = max(row["crap_branch"] for row in rows)
    print(f"functions measured: {len(rows)}")
    print(f"max CRAP_line: {max_line:.3f}")
    print(f"max CRAP_branch: {max_branch:.3f}")
    print(f"strict limit: CRAP < {args.max_crap:g}")

    failures = over_limit(rows, args.max_crap)
    for row in sorted(
        failures,
        key=lambda item: max(item["crap_line"], item["crap_branch"]),
        reverse=True,
    ):
        print(
            f"FAIL {row['path']}:{row['line']} {row['name']}: "
            f"line={row['crap_line']:.3f}, branch={row['crap_branch']:.3f}"
        )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
