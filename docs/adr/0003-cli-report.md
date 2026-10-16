# ADR 0003: deterministic CLI coverage report

## Status

Accepted for the bounded CLI interface, 2026-10-15.

## Context

The Python API reads a LUCAS support GeoPackage and measures one OSM polygon. A user
also needs a reproducible command that records its inputs, source pin, per-support
evidence, and taxonomy decisions without downloading or publishing source data.

## Decision

`lucas-cover` reads one UTF-8 WKT polygon from a file or standard input. It requires
`--osm-crs EPSG:3035`; other CRS values are rejected. The command reads the bundled
source manifest, opens the user-supplied GeoPackage read-only, and uses the polygon
bounds for a conservative RTree query. The calculator performs exact intersections.
The command does not reproject, repair, or modify input geometries.

The JSON document uses schema name `lucas_polygon_coverage.v1`. Its output includes:

- SHA-256 of the exact WKT bytes and the full OSM area.
- Source asset, layer, CRS, and expected SHA-256 from the package manifest.
- Query CRS and bounds, `eligible_bbox_candidate_count`, and rejection reasons.
  The count covers eligible rows selected by the conservative RTree window; the
  `supports` array is the exact-intersection evidence.
- Every support that touches the OSM polygon, with feature ID, point ID, raw support
  class, survey date, target coordinates, observer GPS, core LC/LU labels, intersection
  area, and a boundary-only flag.
- Per-level class areas and fractions, gate outcomes, unmapped feature IDs, and
  positive-area overlap evidence.

The JSON includes no timestamp or local file path. It labels the manifest digest as
`expected_sha256` and sets `sha256_verification` to `not_performed_by_cli`. It does
not hash the user-supplied GeoPackage during each run.
When a file output is requested, the CLI rejects output paths that resolve to or
identify either input file, including symlink and hard-link aliases.
It writes to a temporary file in the resolved destination directory and replaces
the destination directory entry atomically, protecting inputs from a late change
to that entry after the alias checks.
The CLI applies the approved direct floating comparison `fraction >= 0.80`; it does
not apply a ULP tolerance. Same-class geometries are unioned before measurement, and
multiple classes at the gate produce an ambiguous result.

The source GeoPackage stays outside the source distribution and Docker build context.
The container provides the CLI but contains no LUCAS data.

## Consequences

A JSON report can be compared across repeated runs when its WKT and GeoPackage bytes
are unchanged. Users can verify the source digest independently. The command does not
make a full national run; callers provide one OSM polygon at a time.
