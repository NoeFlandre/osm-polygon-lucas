# Supplemental LUCAS point validation

The point method is a separate supplemental analysis. It does not change the
polygon-area method or its strict 80% coverage threshold.

The [LUCAS Copernicus 2022 protocol paper](https://essd.copernicus.org/articles/16/5723/2024/)
states that the surveyor records the Copernicus class at the position reached;
that position may differ from the theoretical LUCAS sample point. The point
reader uses `survey_gps_lat`, `survey_gps_long`, and the Copernicus module class
`surveycprnlc` as coordinate and class evidence. It also reads observation
identifiers and quality fields for the filters below. It does not load
`point_lat`, `point_long`, or `survey_lc1` into the point method.

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

The Wiki source has Wikipedia and Wikivoyage input streams. Coalesce each
stream's regional partitions, then merge both streams by their shared
`sentence_id` key before sampling. Duplicate keys with different labels fail
the run; matching keys union their polygon links and retain all stream
provenance. The configured quota and H3 cell cap therefore apply to the combined
Wiki cohort, while the report keeps per-stream counts and selected-row
provenance.

The sampler targets 500 `yes` and 500 `no` sentence instances. It assigns each
eligible anchor point directly to an H3 resolution-3 cell, ranks stable
source-qualified sentence keys with seed 42, and spreads picks round-robin over
occupied cells. The combined sample is capped at 20 instances per cell. Reports
record the H3 binding version, resolution, cell ID, and anchor polygon. If a quota
cannot be met under the observed pool and geographic cap, the report exposes
that shortfall; it does not widen eligibility or change labels. Sentence text
and sample manifests remain local and are not committed.

The point census and sample utilities are read-only. They do not start
inference, change source datasets, or claim a full-corpus result until a complete
pinned run is available.
