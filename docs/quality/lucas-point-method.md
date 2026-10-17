# Supplemental LUCAS point validation

The point method is a separate supplemental analysis. It does not change the
polygon-area method or its strict 80% coverage threshold.

The [LUCAS Copernicus 2022 protocol paper](https://essd.copernicus.org/articles/16/5723/2024/)
states that the surveyor records the Copernicus class at the position reached;
that position may differ from the theoretical LUCAS sample point. The point
reader therefore selects only `survey_gps_lat`, `survey_gps_long`, and the
Copernicus module class `surveycprnlc`. It does not load `point_lat`,
`point_long`, or `survey_lc1` into the point method.

## Frozen observation policy

- Use `survey_year == 2022`, `point_copernicus == 1`, and
  `surveycprncando == 1`.
- Accept and separately count the three recognized `survey_obs_type` modes:
  field survey with the point visible within 100 m, field survey with the point
  visible beyond 100 m, and photo-interpretation in the field. Missing and
  unknown modes are excluded and counted.
- Use the package's versioned LUCAS C3 mapping at an explicitly named taxonomy
  level. The report includes a SHA-256 digest of the exact mapping.
- Interpret GPS pairs as longitude/latitude in OGC:CRS84. Source polygons must
  use the same CRS. The point method does not reproject, repair, or snap points.
- A boundary point is excluded. Points outside all polygons and points covered
  by more than one polygon are also excluded and counted.
- Missing, malformed, and taxonomy-unmapped labels are counted. A polygon needs
  at least one mapped observation; it is eligible only when all of its mapped
  observations agree at the selected taxonomy level. Conflicting polygons are
  excluded and reported. Missing or unmapped observations do not override the
  agreement check among mapped observations.
- Count each source polygon once. GeoPackage `fid` is the observation record
  key; repeated LUCAS `point_id` values are not silently collapsed. Exact
  repeated records with the same feature ID are ignored, while conflicting
  records with that ID fail the run.

## Sentence sampling

For each source, join its existing `yes`/`no` sentence labels only to point-
eligible polygon IDs. Sentence keys linked to multiple eligible polygons are
counted once and anchored to the lexically smallest eligible polygon ID. The
sampler keeps the selected sentence IDs grouped by anchor polygon for clustered
analysis and reports both sentence and polygon counts.

The sampler targets 500 `yes` and 500 `no` sentence instances. It projects
representative points of eligible source polygons to EPSG:3035, spreads picks
round-robin over occupied 50 km cells, and caps the combined sample at 20
instances per cell. It uses seed 42 by default. If a quota cannot be met under
the observed pool and geographic cap, the report exposes that shortfall; it
does not widen eligibility or change labels. Sentence text and sample manifests
remain local and are not committed.

The point census and sample utilities are read-only. They do not start
inference, change source datasets, or claim a full-corpus result until a complete
pinned run is available.
