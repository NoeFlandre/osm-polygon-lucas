# GeoPackage loader mutation triage

## Current hosted mutation survivors

The mutation job compares every surviving mutant ID against this table. An undocumented, stale, or duplicate ID fails the job. Each listed case has a behavior-equivalence or domain-based rationale.

| Mutant ID | Reason |
| --- | --- |
| `osm_polygon_lucas.cli.x__report_text__mutmut_30` | `None` and `False` are both falsey. `json.dumps` rejects non-finite numbers in either case. |
| `osm_polygon_lucas.cli.x__read_osm_wkt__mutmut_10` | Python resolves `UTF-8` and `utf-8` to the same codec. |
| `osm_polygon_lucas.cli.x__source_manifest__mutmut_11` | Python resolves `UTF-8` and `utf-8` to the same codec. |
| `osm_polygon_lucas.cli.x__create_temporary_directory__mutmut_8` | The uppercase prefix retains the leading dot and keeps the directory unique beneath the output parent; prefix case does not affect collision checking, cleanup, or report behavior. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_12` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_13` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_38` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_39` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_41` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_11` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_12` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_14` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_15` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_rtree__mutmut_10` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__select_rows__mutmut_10` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__select_rows__mutmut_13` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__wkb_order__mutmut_14` | For supported WKB types 1-7, byte 1 is nonzero for little-endian and zero for big-endian, so this test selects the same byte order as the marker at byte 0. |
| `osm_polygon_lucas.support_eligibility.x_classify_support_row__mutmut_67` | A non-string support class adds a rejection reason, and a missing polygon adds a geometry reason. The final type and polygon checks cannot change the result after those checks. |
| `osm_polygon_lucas.polygon_coverage.x__candidate_overlap_pairs__mutmut_11` | For array input, Shapely returns a `(2, n)` index matrix. Both `zip` inputs therefore have length `n`, so changing `strict` cannot change valid query results. |
| `osm_polygon_lucas.polygon_coverage.x__candidate_overlap_pairs__mutmut_14` | For array input, Shapely returns a `(2, n)` index matrix. Both `zip` inputs therefore have length `n`, so changing `strict` cannot change valid query results. |
| `osm_polygon_lucas.polygon_coverage.x__candidate_overlap_pairs__mutmut_17` | For array input, Shapely returns a `(2, n)` index matrix. Both `zip` inputs therefore have length `n`, so changing `strict` cannot change valid query results. |

## Historical preserved run status

The preserved mutation run for `src/osm_polygon_lucas/lucas_gpkg.py` generated 406 mutants: 368 were killed and 38 survived. Its raw status metadata and log are local-only scratch artifacts and are not included in this repository. The implementation code was unchanged when the diagnostic regression tests below were added.

An independent review classified 25 survivors as behavior-equivalent under the loader contract and 13 as diagnostic gaps. The 13 diagnostic gaps now have focused assertions in `tests/test_lucas_gpkg.py`. A separate focused run with the pinned Python 3.12/mutmut 3.8.0 environment executed those 13 mutants; all 13 were killed. It generated a catalog of 749 mutants but did not execute the other 736, so this is not a new overall mutation score or a rerun of the preserved run-7. The complete synthetic suite also passes in the pinned environment. See `docs/quality/mutation-environment.md` for setup, commands, and scope.

## Focused CLI and geometry mutation pass (2026-10-15)

A fresh disposable `/tmp` workspace mutated only `src/osm_polygon_lucas/cli.py` and `src/osm_polygon_lucas/polygon_coverage.py`. The selected tests were `tests/test_cli.py` and `tests/test_polygon_coverage.py`. Mutmut generated 516 mutants: 459 were killed and 57 survived; no mutant timed out or errored.

The survivors were reviewed by category. Strict-zip changes in support-evidence and class-grouping loops are equivalent because both paired sequences are built from the same supports. Geometry-validator survivors change diagnostic wording only; the area-predicate mutation is killed by a positive polygon smaller than one square unit. CLI survivors change help or diagnostic text, JSON formatting, UTF-8 spelling/defaults for the ASCII JSON payload, or an equivalent falsey `allow_nan` setting. Tests cover the behavior-sensitive cases: bounded RTree selection, CRS and required arguments, null dates, unmapped labels, sub-square-unit overlap evidence, and rejection of non-finite JSON values. The source-manifest test asserts the exact package and case-sensitive resource name independently of the host filesystem.

## Historical behavior-equivalent survivors

“Equivalent” here means no change to acceptance, rejection, or decoded values for inputs supported by the loader contract. Error class and feature/item identification remain intact; wording that describes the internal rejection stage is not part of that contract.

| Mutants | Reason |
| --- | --- |
| `_check_layer`: 12, 13, 38, 39, 41; `_check_crs`: 11, 12, 14, 15; `_check_rtree`: 10; `_select_rows`: 10, 13 | These change the case of SQL keywords or identifiers. SQLite treats the changed tokens case-insensitively; the SQL string values and parameters are unchanged. |
| `_wkb_start`: 24, 27, 30 | These vary `int.from_bytes(..., signed=...)`. EPSG:3035 is positive and decodes identically. Other signed or unsigned values still fail the SRS equality check; only the number in its diagnostic may differ. |
| `_wkb_start`: 38; `_check_signature`: 1, 2; `_wkb_order`: 1, 2 | These move a truncation check by one byte. Inputs at the changed boundary still fail as malformed header/envelope/WKB with `ValueError` and the feature id; only which validation stage reports it changes. |
| `_wkb_order`: 14 | For supported 2D WKB type codes 1–7, byte 1 is nonzero for little-endian and zero for big-endian, so testing it selects the same byte order as the marker at byte 0. Unsupported or malformed encodings remain rejected by type validation or Shapely decoding. |
| `_normalize_row`: 5, 8, 9 | These alter the strict zip behavior. `_select_rows` selects exactly `fid`, `geom`, and `REQUIRED_KEYS`; the unpacked `values` therefore always have the same length as `REQUIRED_KEYS`. |
| `_check_signature`: 9 | The magic-byte error displays one extra byte. Its predicate and all acceptance/rejection behavior are unchanged. |

## Historical diagnostic survivors now covered

| Mutants | Permanent assertion |
| --- | --- |
| `iter_lucas_2022_supports`: 31 | SQLite scan failures retain the source path and original cause. |
| `_parse_bounds`: 7; `_bound_values`: 5; `_finite_number`: 3, 6, 9 | Invalid bounds identify the failed condition, arity, number type, overflow, or non-finite value. |
| `_open_read_only`: 2, 11 | Missing-file and SQLite-open failures retain the requested path; open failures retain the cause. |
| `_check_layer`: 6, 32; `_check_crs`: 5 | Missing GeoPackage metadata rows identify the expected table/row. |
| `_check_signature`: 14 | Invalid version reports the version byte. |
| `_wkb_order`: 9 | Invalid byte order reports the byte-order marker. |

The corresponding tests are `test_invalid_bounds_keep_actionable_diagnostics`, `test_missing_file_error_includes_the_requested_path`, `test_sqlite_open_error_keeps_path_and_cause`, `test_sqlite_scan_error_keeps_path_and_cause`, `test_missing_metadata_rows_name_the_expected_item`, `test_bad_version_diagnostic_reports_the_version_byte`, and `test_bad_wkb_byte_order_diagnostic_reports_the_offending_byte`.

## Historical verification after triage tests

- Full synthetic suite: 199 passed on repeat runs, including a run with `PYTHONHASHSEED=20261015`.
- Line and branch coverage: 100% across the five implementation modules; the separate `__main__` wrapper is not included in that figure.
- Ruff lint, Ruff format check, and `ty` check: pass.
- Architecture and complexity pytest checks: pass as part of the full suite.
- Maximum cyclomatic complexity: 5. Maximum line and branch CRAP: 5.0.

All tests use synthetic GeoPackages in temporary directories. This report covers code-quality checks and is not a LUCAS/OSM benchmark result.
