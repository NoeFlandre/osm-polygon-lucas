# LUCAS polygon coverage

This package reads the 2022 LUCAS Copernicus surveyed-area layer and measures class
coverage for one OSM polygon. It provides a read-only GeoPackage loader, the official
C3 taxonomy, a coverage calculator, and a JSON command-line report. It does not fetch
OSM features or run a full-country evaluation.

## Policy B

For each hierarchy level, the calculator groups supports by their mapped class and
unions each class geometry. It divides the class intersection area by the full area of
the original OSM polygon. The class gate is an inclusive `fraction >= 0.80` comparison
with no numeric tolerance. Missing support remains in the denominator. A tie or two
classes above the gate is ambiguous. The result keeps support overlaps and
boundary-only contacts as evidence.

The parser keeps the target `point_lat/point_long`, survey observer GPS, core `LC` and
`LU` labels, and polygon `surveycprnlc` support class in separate fields. Valid
`survey_date` values from 2023 remain in the result.

## Use the CLI

```sh
uv run lucas-cover \
  --osm-wkt osm-polygon.wkt \
  --osm-crs EPSG:3035 \
  --lucas-gpkg data/raw/l2022_survey_cop_radpoly_attr.gpkg \
  --output coverage.json
```

The command requires EPSG:3035 and does not reproject or repair geometry. It records
the input WKT SHA-256 and the expected source SHA-256 from the package manifest. It
does not hash the GeoPackage on each run.

## Source

The source is the JRC asset `l2022_survey_cop_radpoly_attr.gpkg`, layer
`l2022_cop_radpoly_radqs_allatr_wcol`, EPSG:3035. Its size, expected SHA-256, schema,
and eligibility rules are recorded in `config/lucas-2022-source.json`. The original
GeoPackage is not included in the Python package, source distribution, or Docker build
context.

## Decisions

- [Support coverage and data model](adr/0001-lucas-support-coverage.md)
- [GeoPackage loader and C3 taxonomy](adr/0002-lucas-gpkg-loader.md)
- [CLI JSON report](adr/0003-cli-report.md)
- [Mutation environment](quality/mutation-environment.md)
- [Mutation triage](quality/mutation-triage.md)
