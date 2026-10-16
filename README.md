# osm-polygon-lucas

A focused Python library for reading the 2022 LUCAS Copernicus surveyed-area
GeoPackage and measuring its support for OSM polygons. This slice provides source
eligibility, the official LUCAS C3 hierarchy, and per-polygon coverage evidence. It
does not download source data, fetch OSM, or run a national evaluation.

## Spatial policy

At each caller-supplied taxonomy level, support geometries are grouped by class and
unioned before area measurement. The class fraction is
`area(OSM ∩ union(class supports)) / area(original OSM polygon)`. A class meets the
gate when its fraction is at least `0.80`; uncovered parts stay in the denominator.
The OSM polygon is not clipped or repaired. The calculator compares the raw floating-
point fraction directly with `0.80`. It does not round the value or add a tolerance.
The exact `0.80` test passes. A computed value of `0.7999999999999999` fails. Exactly
one class meeting the gate is a winner, multiple classes meeting it are ambiguous, and
no class meeting it produces no winner. Class areas are aggregated at each hierarchy
level before the gate is applied.

The result retains every support feature that touches the OSM polygon, including
zero-area boundary contacts, source feature IDs, survey dates, and support classes.
Positive-area overlaps between supports are reported as same-class, cross-class, or
unmapped evidence. Overlapping cross-class support can therefore produce more than
one class above the gate and an ambiguous result. Point coordinates, observer GPS,
point/core LC, point/core LU, and polygon support class remain separate fields.

## Source and eligibility

The pinned source is the JRC asset
[`l2022_survey_cop_radpoly_attr.gpkg`](https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/LUCAS/LUCAS_2022_Copernicus/l2022_survey_cop_radpoly_attr.gpkg),
layer `l2022_cop_radpoly_radqs_allatr_wcol`, in EPSG:3035. Its expected size and
SHA-256 are recorded in [`config/lucas-2022-source.json`](config/lucas-2022-source.json).
The original GeoPackage is intentionally not distributed in this source tree; the
`data/raw/` directory is ignored by Git.

A support is eligible only when `survey_year=2022`, `point_copernicus=1`,
`surveycprncando=1`, `survey_obs_type=1`, and `surveycprnlc` is non-null. Geometry
must be a valid, positive-area Polygon or MultiPolygon. `survey_date` is retained;
valid dates in 2023 are not filtered out. The loader maps the source observation text
by an exact crosswalk and keeps the target `point_lat/point_long` distinct from
`survey_gps_lat/survey_gps_long`.

## Install and verify

Python 3.11 or newer is required. `.python-version` selects Python 3.12 for this
project. With [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked
uv run --locked pytest
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked ty check src tests
```

Tests build small synthetic GeoPackages in temporary directories. They do not read
or download the 1.1 GB source asset. `uv.lock` pins the development environment; the
package runtime dependency range is `shapely>=2.0,<3`.

## Python API

```python
from osm_polygon_lucas import (
    EligibleSupport,
    LUCAS_C3_LEVELS,
    assess_osm_polygon,
    iter_lucas_2022_supports,
)

# Both geometries must use EPSG:3035. The bounds are minx, miny, maxx, maxy.
results = iter_lucas_2022_supports(
    "data/raw/l2022_survey_cop_radpoly_attr.gpkg",
    bounds=osm_polygon_epsg3035.bounds,
)
supports = [item for item in results if isinstance(item, EligibleSupport)]
coverage = assess_osm_polygon(osm_polygon_epsg3035, supports, LUCAS_C3_LEVELS)
for level in coverage.levels:
    print(level.level, level.status, level.winner, level.classes)
```

The loader yields one `EligibleSupport` or `Rejection` for each selected source
feature. Its optional RTree bounds are a conservative prefilter; the calculator
performs exact intersections. Neither API reprojects geometry, repairs invalid
geometry, nor infers classes from prefixes. Use the `survey_date` and raw evidence
fields when matching or auditing results.

## Command line

The CLI accepts one WKT polygon and requires an explicit EPSG:3035 CRS. It uses the
GeoPackage RTree for a conservative window query, then measures exact intersections.
It writes deterministic JSON with the input WKT hash, pinned source hash, support
feature evidence, point and observer coordinates, core LC/LU labels, class areas,
fractions, overlaps, and level decisions. The report labels the source digest as the
expected SHA-256 from the manifest; the CLI does not rehash the 1.1 GB GeoPackage on
every run.

```sh
uv run lucas-cover \
  --osm-wkt osm-polygon.wkt \
  --osm-crs EPSG:3035 \
  --lucas-gpkg data/raw/l2022_survey_cop_radpoly_attr.gpkg \
  --output coverage.json
```

The raw source file is not part of the Python package or Docker build context; supply
it separately. The `Dockerfile` describes the runtime image, but this publication
check does not verify a Docker build.

## Documentation

Build the documentation with `uv run --group docs mkdocs build --strict`. Its entry
page is [`docs/index.md`](docs/index.md). The public API and data model are described
in the [coverage ADR](docs/adr/0001-lucas-support-coverage.md), the
[loader ADR](docs/adr/0002-lucas-gpkg-loader.md), and the
[CLI report ADR](docs/adr/0003-cli-report.md).

No LICENSE file is included. The pinned LUCAS GeoPackage is not distributed here and must be supplied separately.
