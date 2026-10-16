"""Measure per-function CRAP scores and enforce the approved strict limit."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any


def crap(complexity: int, coverage: float) -> float:
    """Return the CRAP score for cyclomatic complexity and coverage ratio."""
    return complexity**2 * (1 - coverage) ** 3 + complexity


def _ratio(executed: int, missing: int) -> float:
    total = executed + missing
    return 1.0 if total == 0 else executed / total


def _function_blocks(
    blocks: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield Radon function blocks paired with coverage.py qualified names."""
    seen: set[tuple[str, int, int]] = set()

    def visit(
        block: dict[str, Any], parent_name: str | None = None
    ) -> Iterator[tuple[dict[str, Any], str]]:
        if block["type"] == "class":
            for method in block.get("methods", []):
                yield from visit(method, block["name"])
            return
        if block["type"] not in {"function", "method"}:
            return

        owner = block.get("classname") or parent_name
        if owner:
            coverage_name = f"{owner}.{block['name']}"
        elif parent_name:
            coverage_name = f"{parent_name}.{block['name']}"
        else:
            coverage_name = block["name"]
        identity = (coverage_name, block["lineno"], block["endline"])
        if identity not in seen:
            seen.add(identity)
            yield block, coverage_name
        for closure in block.get("closures", []):
            yield from visit(closure, coverage_name)

    for block in blocks:
        yield from visit(block)


def _coverage_ratios(function_data: dict[str, Any]) -> tuple[float, float]:
    executed_lines = len(function_data["executed_lines"])
    missing_lines = len(function_data["missing_lines"])
    if executed_lines + missing_lines == 0:
        raise ValueError("Function coverage report has no executable lines")
    line_coverage = _ratio(executed_lines, missing_lines)
    branch_coverage = _ratio(
        len(function_data["executed_branches"]),
        len(function_data["missing_branches"]),
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
        for block, coverage_name in _function_blocks(blocks):
            try:
                function_data = file_data["functions"][coverage_name]
            except KeyError as exc:
                raise ValueError(
                    f"Coverage report is missing function {coverage_name} in {path}"
                ) from exc
            line_coverage, branch_coverage = _coverage_ratios(function_data)
            complexity = block["complexity"]
            rows.append(
                {
                    "path": path,
                    "line": block["lineno"],
                    "name": coverage_name,
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("radon_json", type=Path)
    parser.add_argument("--max-crap", type=float, default=6.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_crap) or args.max_crap <= 0:
        parser.error("--max-crap must be a finite positive number")

    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    radon = json.loads(args.radon_json.read_text(encoding="utf-8"))
    rows = measure(coverage, radon)
    max_line = max(row["crap_line"] for row in rows)
    max_branch = max(row["crap_branch"] for row in rows)
    print(f"functions measured: {len(rows)}")
    print(f"max CRAP_line: {max_line:.4f}")
    print(f"max CRAP_branch: {max_branch:.4f}")
    print(f"strict limit: CRAP < {args.max_crap:g}")

    failures = over_limit(rows, args.max_crap)
    for row in sorted(
        failures,
        key=lambda item: max(item["crap_line"], item["crap_branch"]),
        reverse=True,
    ):
        print(
            f"FAIL {row['path']}:{row['line']} {row['name']}: "
            f"line={row['crap_line']:.4f}, branch={row['crap_branch']:.4f}"
        )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
