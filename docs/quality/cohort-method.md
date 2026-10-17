# Sentence evaluation cohort method

## Scope and status

The evaluation has nine independent source/reference cohorts: Description,
Website, and Wiki crossed with LUCAS, EUNIS, and WorldCover. Each cohort targets
1,000 sentence instances, split evenly across the source's existing relevance
labels (`yes` and `no`). The target is a quota, not a reason to widen
eligibility or relabel rows. Report the eligible `yes` and `no` pool sizes
before committing to achievable sample counts. Any shortfall remains visible.

The current census work measures only the three source pools against LUCAS.
The other six source/reference combinations remain blocked by unresolved EUNIS
policy and missing WorldCover assignments.

`tools/lucas_description_census.py` implements the read-only Description ×
LUCAS census for Austria and Belgium. It performs spatial eligibility before
joining label rows, and writes a compact local receipt with exact counts,
exclusion totals, pinned input hashes, and deterministic decision digests. It
does not sample, read sentence text, run inference, or modify its input files.
The tested runtime was Python 3.12.11, PyArrow 26.0.0, pyproj 3.8.0, and
Shapely 2.2.0. The bounded receipt is at
`data/quality/lucas-description-ab-census.json` (git-ignored).

A separate local receipt records a 110-partition Europe Description census
with 4 eligible `yes` and 1 eligible `no` sentence instances. Its checkpoint
driver is git-ignored, so the Europe aggregate has not passed tracked-run
reproduction. `tools/lucas_source_pool_census.py` now supports the 110-region
scope for Description, Website, and Wikidata/Wikipedia source data. It can read
either Atlas outputs or exact-revision Hugging Face snapshots already present
in the local Hub cache. Direct mode does not download files, read sentence
text, or require Atlas. The spatial census has not been run from those
snapshots; see `docs/quality/sentence-evaluation-readiness.md` for the open
reproduction gate and manifest counts.

Install the bounded-census dependencies with `uv sync --extra census`. Before
spatial processing, each runner streams the pinned LUCAS GeoPackage through
SHA-256 and checks its digest and byte size against
`config/lucas-2022-source.json`. Each spatial receipt records that both checks
ran for the asset it used. The runners also pin the file's device, inode, size,
and modification/change timestamps and reject a changed asset between or during
regional scans.

The `wiki` source is backed by the pinned Wikidata/Wikipedia repository. Atlas
stores Wikipedia and Wikivoyage sentence streams in separate partitions; the
census preserves that split and joins each stream by `sentence_id`.

### Direct source snapshots

Direct mode accepts `--source-snapshot` and `--label-snapshot` paths that end
at `datasets--OWNER--REPO/snapshots/<configured-revision>` in the existing HF
Hub cache. It validates both repository names and 40-character revisions
against `config/lucas-source-pools.json`; it never calls the Hub or downloads
missing files. Select only needed source Parquet columns: Description keys and
`sentence_count`, Website polygon IDs and sentence counts, Wiki sentence and
document IDs, geometry, and source-label join fields. The sentence arrays and
text columns are never requested. Wiki joins through document-to-polygon
links, and Description polygons use stable `osm:<type>/<id>` keys.

For example, with the two exact snapshots already cached, run one small
partition with:

```sh
uv run --locked --extra census python tools/lucas_source_pool_census.py \
  --source Description \
  --source-snapshot "$HF_HUB_CACHE/datasets--NoeFlandre--osm-polygon-description-tag/snapshots/b4706eb315c66a8a57135289e7612dca8e03caf8" \
  --label-snapshot "$HF_HUB_CACHE/datasets--NoeFlandre--osm-polygon-description-tag-landuse/snapshots/ce98b328f3c6543800a3d931877b51eb3f0cf768" \
  --lucas-gpkg data/raw/l2022_survey_cop_radpoly_attr.gpkg \
  --region albania --output data/quality/description-albania.json
```

The direct report records snapshot pins, relative file paths, row counts,
runtime versions, and deterministic eligibility/decision digests. It contains
no source sentence text. No direct snapshot census has been run for this PR.
For long runs, pass `--checkpoint-dir` to save atomic per-region geometry and
join results outside both HF snapshot roots. Checkpoints are keyed by the pinned
inputs, LUCAS asset signature, census and coverage-module hashes, package export
hash, and Python-library plus GEOS/PROJ data versions, so implementation or
environment changes trigger a fresh calculation.
Partial-region runs report
`shortfall_from_target: null`; a numeric shortfall is reported only when all
configured regions are selected and the eligible pool counts are complete.

The source-label decision and the reference-coverage decision are separate.
The 500/500 split uses existing source-side relevance labels; it does not use
model predictions or a reference product to redefine those labels. A sentence
may occur in more than one source/reference cohort when it independently meets
each cohort's eligibility rules. Preserve the overlap and report it.

## Candidate generation order

1. Read pinned OSM polygon keys and geometries. Keep the data revisions with
   every key; identifiers from different sources must not collide. Do not join
   sentence labels at this stage.
2. Intersect each polygon with the pinned support or reference geometries.
   Generate a separate candidate pool for each source/reference pair and
   classify coverage using that reference's resolved eligibility rule.
3. Filter text-unit polygon links to polygons with an eligible reference code.
   Only then join the pinned source's existing `yes`/`no` sentence relevance
   labels using the documented source join key. These source-side labels remain
   separate from reference codes.
4. Count eligible source-relevance `yes` and `no` rows within each reference
   cohort. Check feasibility against 500 of each. Do not sample to the quotas
   until these counts have been recorded.
5. Apply geographic sampling to the eligible pool. Use H3 resolution 3, seed
   42, and at most 20 selected instances per H3 cell. Rank stable source-qualified
   keys deterministically within each label and cell, then spread picks across
   occupied cells. Record the anchor and H3 cell. For a sentence linked to
   several eligible polygons, count the sentence once in a given cohort and
   preserve all qualifying polygon links; choose the geography anchor by a
   documented stable key rule.
6. Save a manifest with source, label, reference, and geometry revisions;
   eligibility rules; eligible counts; selected counts; shortfalls; seed; H3
   version/resolution; and stable sentence keys. Do not put sentence text,
   raw polygon geometry, or evaluation results in Git.

The 15 existing Malta pilot cases remain regression fixtures. The five-polygon
Malta run produced zero cases meeting the LUCAS 80% eligibility gate; it tests
the pipeline only and says nothing about EU-wide feasibility. Do not repeat an
arbitrary geography pilot to infer eligible-pool sizes.

## Reference-specific coverage rules

### LUCAS 2022

Use the official LUCAS support GeoPackage and the package's existing support
coverage API. Transform OSM polygon geometries from OGC:CRS84 to EPSG:3035 with
`always_xy=True`. The 80% threshold is measured against the full original OSM
polygon area. A polygon is eligible only when exactly one mapped
`main_category` code meets that gate; the code is its LUCAS reference label.
Below-threshold polygons have no resolved reference label and are excluded,
along with all source sentence labels linked only to them. They are not LUCAS
`no` examples. Ambiguous, unknown-unmapped, no-overlap, boundary-only, failed,
and skipped outcomes are also excluded; none becomes a reference label by
default. The tracked source-pool report lists geometry exclusions: invalid
WKB or GeoJSON, non-polygon geometry, out-of-bounds CRS84 coordinates, invalid
geometry, zero area, and invalid or zero-area projected geometry. Existing
sentence `yes`/`no` values are source-relevance labels and remain separate from
LUCAS codes. Uncovered polygons are never negative examples.

### WorldCover

Pin the WorldCover release and assignment output before joining. The planned
rule uses the existing 80% dominance threshold over the full original OSM
polygon area. The EU assignment outputs are not currently available in the
workspace, so no WorldCover eligible counts or cohort rows can be claimed.

### EUNIS

The available reference is the EEA 2021 probability map, with largest
intersection noted as a provisional assignment method. The probability
threshold has not been decided. Treat these assignments as proxy labels, not
ground truth, and do not count EUNIS rows as evaluation eligible until the
threshold and assignment policy are approved and pinned.

## Source and revision pins

Source input/label repository revisions and the LUCAS source asset metadata are
listed in `config/cohort-sources.json` and `config/lucas-2022-source.json`.
The three LUCAS source schemas, stream paths, region scope, geometry encodings,
join keys, threshold, and exclusions are pinned in
`config/lucas-source-pools.json`. The availability report totals come from
manifest metadata before spatial filtering. A spatial report records its
runtime versions and hashes for the plan, manifests, polygon outputs, and
coverage implementation.
Reference assignment manifests, EUNIS threshold, and output checksums are
deliberately null or pending until the corresponding data and decisions exist.
Never silently substitute a different source, reference release, or revision.

## Deferred work and boundaries

Separate polygon `all` and reference-code-stratified ablations remain tracked
but deferred. The `all` sample is drawn independently from reference labels;
reference-code strata use only eligible LUCAS polygons. No binary LUCAS `yes` or
`no` stratum is defined by this rule.

Do not start inference or request Grid5000 compute until all nine eligible
`yes`/`no` counts, pinned manifests, and policy preflight are complete. Run the
Grid5000 policy check on a frontend before any allocation request. The prior
Wiki-only throughput measurement is a planning estimate, not evidence for
these cohorts. No compute allocation or external outreach is authorized by
this plan.
