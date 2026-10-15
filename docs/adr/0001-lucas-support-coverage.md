# ADR 0001: LUCAS 2022 support coverage for OSM polygons

## Status

Accepted for the bounded LUCAS loader, taxonomy, and Policy B calculator slice, 2026-10-15.

## Context

LUCAS 2022 publishes a Copernicus radius polygon for each survey point. Each polygon carries a support class in `surveycprnlc`. This project asks whether one support class covers enough of an OSM polygon to label that polygon at a taxonomy level.

The source facts are pinned in `config/lucas-2022-source.json`. The verified asset is `data/raw/l2022_survey_cop_radpoly_attr.gpkg`, layer `l2022_cop_radpoly_radqs_allatr_wcol`, in EPSG:3035. The local 1,121,144,832-byte copy has SHA-256 `4d240c5710866f9549712685859a5051522b223f3e1fe580dcc9e398b68b037f`; SQLite integrity check returned `ok`. Geometry metadata declares `MULTISURFACE`; the filtered candidates contain 126,522 Polygons and one MultiPolygon.

## Decision

The package has a source boundary, a taxonomy table, and a pure calculator.

- `lucas_gpkg` opens the pinned GeoPackage read-only, validates its schema and EPSG:3035 metadata, maps source text fields, and yields one `EligibleSupport` or `Rejection` per feature.
- `support_eligibility` parses one normalized JRC row into an `EligibleSupport` or a `Rejection`.
- `lucas_taxonomy` defines exact maps for the official 2022 C3 observed classes at the main-category, second-level, and observed-code levels.
- `polygon_coverage` measures one OSM polygon against eligible supports and returns source evidence and one decision per taxonomy level.

Approved Policy B governs the calculator.

1. For each supplied level, group the touching supports by their label at that level.
2. Union each group, intersect the union with the full OSM polygon, and divide by the full OSM area.
3. A class meets the gate when its computed fraction is at least 0.80. The code compares the raw floating-point fraction directly with `0.80`. It does not round the fraction or allow a tolerance. The test accepts an exact `0.80` and rejects `0.7999999999999999`, the next representable float below it.
4. Missing support stays in the denominator. There is no total-support gate.
5. Exactly one passing class is the winner. Two or more passing classes make the level `ambiguous` with no winner. No passing class gives no winner.
6. Evidence keeps every touching support, including boundary-only contacts. It also keeps overlaps measured inside the OSM polygon, labelled `same_class`, `cross_class`, or `unmapped`, and the unmapped supports for each level.

Taxonomy maps raw classes by exact code. The official C3 table contains 74 observed codes. Heading-only codes and unlisted values stay unmapped. The code never infers a label from a prefix.

## Caller usage

```python
from osm_polygon_lucas import (
    EligibleSupport,
    LUCAS_C3_LEVELS,
    assess_osm_polygon,
    iter_lucas_2022_supports,
)

supports = [
    result
    for result in iter_lucas_2022_supports(
        gpkg_path, bounds=osm_bounds_epsg3035
    )
    if isinstance(result, EligibleSupport)
]
coverage = assess_osm_polygon(
    osm_polygon_epsg3035, supports, LUCAS_C3_LEVELS
)
decision = coverage.levels[0]
```

`bounds` is an optional `(minx, miny, maxx, maxy)` window in EPSG:3035. The loader uses the layer RTree as a conservative bounding-box prefilter. The calculator performs the exact intersection. The caller projects the OSM polygon to EPSG:3035. Neither module repairs or reprojects geometry.

## Types and API

Parser, in `support_eligibility`:

- `classify_support_row(source_feature_id, row, geometry) -> EligibleSupport | Rejection`.
- `EligibleSupport` holds `source_feature_id`, `point_id`, `support_class`, `survey_date`, `target_coordinates`, `observer_gps`, `core_labels`, and `geometry`.
- `Coordinates(lat, long)` holds one coordinate pair. `target_coordinates` comes from `point_lat` and `point_long`. `observer_gps` comes from `survey_gps_lat` and `survey_gps_long`.
- `CoreLabels(survey_lc1, survey_lc2, survey_lu1, survey_lu2)` holds point and core labels. These never replace `support_class`.
- `Rejection(source_feature_id, reasons)` lists every failed criterion, in criterion order. `RejectionReason` names each criterion.
- Missing keys and wrongly typed carried fields raise on every row. Failed criteria return a `Rejection`.

Calculator, in `polygon_coverage`:

- `TaxonomyLevel(name, labels)` maps raw class to label for one level.
- `assess_osm_polygon(osm_polygon, supports, levels) -> PolygonCoverage`.
- `PolygonCoverage(osm_area, supports, levels)`. `levels` follows the supplied order.
- `SupportEvidence` holds `source_feature_id`, `point_id`, `raw_class`, `survey_date`, `target_coordinates`, `observer_gps`, `core_labels`, `intersection_area`, and `boundary_only`.
- `LevelDecision` holds `level`, `status`, `winner`, `classes`, `unmapped_feature_ids`, and `overlaps`.
- `ClassCoverage` holds `label`, `source_feature_ids`, `covered_area`, `fraction`, and `meets_gate`.
- `Overlap` holds `first_feature_id`, `second_feature_id`, `area`, and `relation`.
- `LevelStatus` is `winner`, `ambiguous`, or `no_class_meets_gate`.

Loader and taxonomy, in `lucas_gpkg` and `lucas_taxonomy`:

- `iter_lucas_2022_supports(path, *, bounds=None)` streams `EligibleSupport | Rejection` values in `fid` order. The caller can filter for `EligibleSupport` before passing supports to the calculator.
- `LUCAS_C3_LEVELS` is an ordered tuple of three `TaxonomyLevel` values: `main_category`, `second_level`, and `observed_code`.
- The loader uses GeoPackage `fid` as the source feature ID. It preserves `point_id`, `point_lat/long`, observer GPS, point/core LC and LU labels, polygon support class, survey date, and geometry as separate values.
- Attribute eligibility uses `survey_year=2022`, `point_copernicus=1`, `surveycprncando=1`, observation code 1, and non-null `surveycprnlc`. Geometry must be a valid positive-area Polygon or MultiPolygon. The loader does not filter on `survey_date`; it retains calendar dates in 2023.
- Observation strings map by the exact crosswalk in the source manifest. Unknown strings raise `ValueError`. Domain failures return `Rejection`; source and schema faults raise `ValueError` with the feature ID where available.

The OSM polygon must be a valid `Polygon` or `MultiPolygon` with positive area. Otherwise the call raises `ValueError`.

## Source verification

The pinned digest and download metadata are in `config/lucas-2022-source.json`. A read-only scan found 137,966 layer features. Under the attribute eligibility filters, 126,523 candidates have non-null `surveycprnlc`; seven have valid but zero-area geometry, leaving 126,516 positive-area polygonal candidates. No invalid or non-polygonal candidate geometries were found. The layer RTree is `rtree_l2022_cop_radpoly_radqs_allatr_wcol_geom`.

The layer stores `survey_obs_type`, `survey_date`, and observer GPS as text. The loader maps the three observed observation strings to descriptor codes 1, 2, and 3, parses date text to calendar dates, and parses finite observer GPS numbers. Strict eligibility keeps code 1. It does not filter survey dates, so valid 2023 dates remain included. The loader checks both the GeoPackage SRS authority and authority code for EPSG:3035.

## Alternatives considered

A global index with a streaming accumulator could read the layer once and accumulate results for many OSM polygons. This slice keeps the simpler per-polygon calculator and a bounded RTree query in the loader. A full national run may add a separate batching layer after its resource and ordering requirements are measured.

## Remaining scope

- The bounded slice does not run a full OSM evaluation or build a national candidate index.
- Ten source class strings (`-1`, `Bx1`, `Bx2`, `C2`, `D1`, `D2`, `E1`, `E2`, `E3`, `F4`) remain unmapped because no exact official C3 observed-code mapping was verified for them.
- The source evidence and per-level overlap records are available in the calculator result. Point/core LC and LU labels remain separate on each `EligibleSupport` and never replace the polygon support class.
