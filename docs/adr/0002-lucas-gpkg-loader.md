# ADR 0002: LUCAS 2022 GeoPackage loader and C3 taxonomy

## Status

Accepted for the loader and taxonomy slice, 2026-10-15. It extends ADR 0001. The
decisions in ADR 0001 stand.

## Context

ADR 0001 defines the parser and the calculator. It leaves the loader open. This ADR
records the loader that feeds them and the official C3 taxonomy that labels them.

## Decision

`iter_lucas_2022_supports(path, *, bounds=None)` yields one `EligibleSupport` or one
`Rejection` for each feature of the layer, in fid order.

`LUCAS_C3_LEVELS` holds three `TaxonomyLevel` maps: `main_category`, `second_level`,
and `observed_code`. Each label is an official code. Each map is keyed on the 74
observed codes.

The parser and the calculator do not change.

## Source contract

The file opens with `mode=ro`. A missing file raises `FileNotFoundError`, and nothing
is created.

The layer, CRS, and field checks run on the first `next()`. They match the
`gpkg_contents`, `gpkg_geometry_columns`, and `gpkg_spatial_ref_sys` rows, and the 15
required field names, matched exactly.

`survey_date` is text in the form `2022-09-04 00:00:00.000`. The loader keeps the
calendar day. It applies no survey-date filter, so the 1408 rows dated 2023 stay.

`survey_gps_lat` and `survey_gps_long` are text. The loader reads them as finite
floats and rejects `nan` and `inf`. The parser would accept `nan` without complaint.

`survey_obs_type` maps from its three exact strings to 1, 2, or 3 before the parser
runs. The parser rejects text silently, so the mapping must happen first. An unknown
string raises.

Each geometry blob must have the `GP` header, version 0, SRS 3035, and no extended or
reserved flag bits. Its WKB must be a 2D type, codes 1 to 7. Z, M, and EWKB types
raise. Other 2D types reach the parser, which rejects non-polygons.

## Bounds

`bounds` is `(minx, miny, maxx, maxy)` in EPSG:3035. The RTree stores
`(minx, maxx, miny, maxy)`, so the query binds each value by name. The filter keeps
boxes that touch the window, so edge contacts stay in. Exact intersection happens
later, in `assess_osm_polygon`.

## Errors

Source and schema faults raise `ValueError`, and the message names the fid or the item.
A `TypeError` from the parser becomes a `ValueError`, because the loader supplies the
types. Domain outcomes become `Rejection` values. No value maps silently.

## Taxonomy

The table follows the official 2022 C3 grouping. Heading-only codes, such as `B10`, are
labels and never keys. A code that is both a heading and an observed class, such as
`C10`, keys itself.

These strings are not in the official table, so they stay unmapped at every level:
`-1`, `Bx1`, `Bx2`, `C2`, `D1`, `D2`, `E1`, `E2`, `E3`, and `F4`. Lookup is exact.
No code is split by prefix.

## Alternatives considered

- A reader class that owns the connection and the layer description. It adds a second
  entry point and importable internals. The fixed usage needs one call, so it was
  rejected.
- Eager validation at call time. It would hold a connection open before iteration, or
  open the file twice. Validation runs on the first `next()` instead.
- One taxonomy row per observed code, repeating each parent. A reviewer cannot check
  the grouping against the official table at a glance. The chosen rows are one per
  second-level code.
- Prefix matching. It would map `Bx1` into the Cropland tree. It is not allowed.

## Verification

The synthetic suite passes 199 tests on repeat runs, including a run with
`PYTHONHASHSEED=20261015`. Tests build GeoPackages in temporary directories and do not
read `data/raw/`. The suite includes architecture and complexity checks. Ruff lint,
formatting, and `ty` pass. Line and branch coverage are 100% across the five
implementation modules; that figure excludes the `__main__` entry-point wrapper.
Maximum cyclomatic complexity is 5; maximum line and branch CRAP are 5.0.

The preserved mutation run killed 368 of 406 loader mutants. The 38 survivors have
been reviewed: 25 are behavior-equivalent under the loader contract, and 13 concern
diagnostic detail now asserted by focused tests. A focused run with the pinned
Python 3.12/mutmut 3.8.0 environment killed those 13 diagnostic mutants. This was not
a full mutation rerun and does not change the run-7 score; see
`docs/quality/mutation-triage.md` and `docs/quality/mutation-environment.md`.

A bounded read-only sample of the first 400 rows of the real layer gave 367 eligible
rows and 33 rejected rows. The rejections were 24 for `survey_obs_type` and 9 for
`survey_year`. The sample took 0.15 seconds. A window query built from one sampled
feature returned that feature. This is a bounded sample, not a full-layer result.

## Open items

- The full layer has not been streamed in this pass. At the sampled rate, a full pass
  takes about a minute. That figure is an extrapolation.
- Taxonomy coverage of the full layer is not checked. The sample held 42 classes, and
  all 42 are in the official table.
- The `sqlite3.connect` failure path is covered with a simulated `sqlite3.OperationalError`; an actual OS-level open failure was not induced.
- `survey_date` keeps only the calendar day. Whether every time part is midnight in
  the full layer is not checked.
