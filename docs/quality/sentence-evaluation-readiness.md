# Sentence evaluation readiness

## Status

The full nine-cohort evaluation is **not ready for sampling or inference**.
Each source/reference pair still targets 1,000 sentence instances, split 500
existing source-relevance `yes` and 500 `no`. Do not pad a shortfall, change a
label, or loosen a reference rule to force that quota.

## Three-source LUCAS availability

The tracked `tools/lucas_source_pool_census.py` covers
Description, Website, and the Atlas `wiki` source. The latter is backed by the
Wikidata/Wikipedia repository and has separate Wikipedia and Wikivoyage
streams. Their source-specific join keys are `description_identity`,
`tag_key`, and `sentence_index` for Description; `polygon_id`, `field`, and
`sentence_index` for Website; and `sentence_id` for each `wiki` stream.
Description geometries use WKB. Website and `wiki` geometries use GeoJSON.

The manifest-only availability check matched Atlas plan
`cb43a674c81643dadc20b727844c014d06e9e1335820fc8cef243f80d8bb5480` and the
configured 110-region scope. It read manifest metadata, not source sentence
text or Parquet rows.

| Source pool and stream | Complete regions | Source labels before LUCAS filtering (`yes` / `no` / `skipped` / `failed`) | Label rows | Polygon rows | Spatial eligibility |
| --- | ---: | --- | ---: | ---: | --- |
| Description | 110/110 | 333,347 / 135,403 / 39,136 / 6,669 | 514,555 | 427,039 | Not run by the tracked source-pool census |
| Website | 110/110 | 6,029,604 / 21,494,268 / 23,953 / 263,465 | 27,811,290 | 735,075 | Not run |
| Wikidata/Wikipedia, Wikipedia | 2/110 | 88,739 / 161,033 / 2,211 / 4,256 | 256,239 | 1,907 | Partial inputs; not run |
| Wikidata/Wikipedia, Wikivoyage | 0/110 | 0 / 0 / 0 / 0 | 0 | 0 | No completed inputs |

These label and polygon counts are manifest totals before LUCAS eligibility.
They do not measure the eligible `yes` and `no` pools. In particular, the
complete Website manifests do not establish a usable LUCAS cohort. The
Wikipedia stream is partial, and no Wikivoyage manifests were present in this
snapshot.

The spatial method requires exactly one mapped LUCAS `main_category` code to
cover at least 80% of the full original projected OSM polygon area. The
denominator includes uncovered area. Below-threshold, ambiguous,
unknown-unmapped, no-overlap, and boundary-only polygons are excluded, as are
invalid WKB or GeoJSON, non-polygon geometry, CRS84 out-of-bounds, invalid or
zero-area geometry, and geometries that become invalid or zero-area after
projection. Failed and skipped source labels are excluded. Uncovered polygons
never become negative examples. The report includes these exact rules and
exclusions so reviewers can inspect them with each run.

Atlas was still running its shared-drive join phase when I checked its
heartbeat, at 777 of 1,533 partitions. I did not run a spatial census while
that job was active. The full spatial counts remain an open gate.

A bounded Description × LUCAS census is complete for the pinned Austria and
Belgium partitions. A separate Europe receipt covers all 110 candidate
partitions in its declared scope. Both use the full original projected OSM
polygon area as the denominator; only polygons with one LUCAS `main_category`
code at or above 80% are reference-eligible. Below-threshold polygons have no
binary LUCAS `no` label; their linked source sentence labels are excluded from
eligible counts. The Europe result leaves a severe shortfall and does not
support a 1,000-instance cohort.

The Europe receipt records 427,039 polygons: 4 eligible, 4,592 below threshold,
13 with unmapped positive-area support, 422,429 with no overlap, and 1 geometry
excluded after projection. Ambiguous, boundary-only, and all other geometry
exclusion outcomes are zero. Four `yes` and one `no` source sentence instances
join to eligible polygons. The four polygons occur in Denmark (1), Netherlands
(1), Portugal (1), and Sachsen (1); the `no` instance is in the Netherlands.

The Europe receipt is git-ignored at
`data/quality/lucas-description-europe-census.jsonl`. Its per-region counts
reconcile, but the checkpoint driver used for that run is also git-ignored.
The tracked source-pool census now supports the three source schemas across
the configured region scope, but no complete tracked spatial run has reproduced
the receipt. Treat the Europe aggregate as recorded evidence, not an
independently reproducible tracked run; this provenance gap is still open.

## Bounded Austria and Belgium results

LUCAS eligibility requires exactly one mapped `main_category` to cover at least
80% of the full original OSM polygon area. Its category code is the reference
label. A mapped result below 80% is ineligible even when there is no positive
unmapped support. Source `yes` and `no` are sentence-relevance labels and do not
stand in for LUCAS reference codes.

| Region | Source polygons | Eligible reference polygons | Below-threshold excluded | Eligible relevance yes | Eligible relevance no | No overlap | Unknown unmapped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Austria | 8,465 | 0 | 55 | 0 | 0 | 8,409 | 1 |
| Belgium | 12,341 | 0 | 435 | 0 | 0 | 11,906 | 0 |
| Combined | 20,806 | 0 | 490 | 0 | 0 | 20,315 | 1 |

No polygon in these two partitions met the reference eligibility rule, so no
sentence label was joined to an eligible polygon: the eligible source-relevance
counts are 0 `yes` and 0 `no` in each region. The 490 below-threshold polygons
are excluded, not negative examples. Ambiguous, boundary-only, and
geometry-validation exclusions were zero in both regions. The receipt includes
a deterministic below-threshold example per region with its candidate category
code and measured fraction; it is diagnostic evidence only and never eligible.

| Region | Example polygon | Top `main_category` candidate | Full-area fraction | Supporting raw class |
| --- | --- | --- | ---: | --- |
| Austria | OSM way 923832544 | C00 | 0.0054311396 | C21 |
| Belgium | OSM relation 3977259 | B00 | 0.0060872306 | B53 |

These are the highest-coverage examples among the below-threshold polygons in
each region. The candidate codes are not assigned labels because neither
fraction reaches 0.80.

The bounded support scan queried the LUCAS RTree by each region's EPSG:3035
bounds before exact polygon intersections:

| Region | RTree candidate rows | Accepted support rows | Rejected support rows | Rejection reasons |
| --- | ---: | ---: | ---: | --- |
| Austria | 8,383 | 8,008 | 375 | 375 `survey_obs_type` |
| Belgium | 5,147 | 4,267 | 880 | 567 `survey_year`; 393 `survey_obs_type` |

Belgium's reason counts overlap by 80 rows because a support can fail both
criteria; they are not additive. The census receipt records every region's
coverage outcomes, unjoined source-label totals, input hashes, runtime versions,
and deterministic decision digests.

## Europe receipt aggregate

| Measure | Count |
| --- | ---: |
| Candidate Description polygons | 427,039 |
| Eligible LUCAS reference polygons | 4 |
| Below 80% threshold | 4,592 |
| Unknown unmapped positive-area support | 13 |
| No overlap | 422,429 |
| Boundary-only / ambiguous | 0 / 0 |
| Geometry excluded after projection | 1 |
| Eligible source-relevance `yes` / `no` instances | 4 / 1 |
| Shortfall from 500 `yes` / 500 `no` | 496 / 499 |

The support scan queried 234,593 RTree candidates: 215,746 accepted and
18,847 rejected. Rejection reason hits were 16,443 `survey_obs_type` and 2,777
`survey_year`; 373 supports failed both, so reason counts overlap. Label status
totals were 333,347 `yes`, 135,403 `no`, 39,136 `skipped`, and 6,669 `failed`.
Only 4 `yes` and 1 `no` joined to eligible polygons.

The executable path is `tools/lucas_description_census.py`.
The compact Austria and Belgium receipt remains local and git-ignored.

## Remaining source/reference pairs

| Source | LUCAS | EUNIS | WorldCover |
| --- | --- | --- | --- |
| Description | Europe receipt: 4 eligible `yes`, 1 eligible `no`; short by 496/499 against 500 each, and the tracked-run provenance gate remains open | Blocked: threshold and pinned assignment outputs missing | Blocked: matching EU assignment outputs unavailable |
| Website | 110/110 source manifests; spatial eligibility not run | Blocked: threshold and pinned assignment outputs missing | Blocked: matching EU assignment outputs unavailable |
| Wikidata/Wikipedia streams | Wikipedia 2/110; Wikivoyage 0/110; spatial eligibility not run | Blocked: threshold and pinned assignment outputs missing | Blocked: matching EU assignment outputs unavailable |

The Europe receipt's source-label status totals are source-only totals before
LUCAS spatial eligibility and must not be used to set quotas. Website source
manifests are complete in the inspected build, but their LUCAS eligibility was
not computed. The Wikidata/Wikipedia source is partial. Do not download extra
Wiki row groups just to fill this gap.

## Other blockers and next work

- **EUNIS:** the EEA 2021 probability-map threshold and final assignment policy
  remain undecided. Largest intersection is provisional; assignments are proxy
  labels, not ground truth.
- **WorldCover:** the planned ESA WorldCover v200/2021 80% full-area rule is
  recorded, but matching EU assignment outputs are unavailable.
- **Europe receipt provenance:** the 110-partition Description receipt was
  produced by a git-ignored checkpoint driver. The tracked source-pool census
  covers all configured source schemas, but a full spatial run is pending.
  Rerun after Atlas stops writing to the shared drive.
- **Sampling:** H3 resolution 3, seed 42, maximum 20 instances per cell applies
  only after all nine eligible yes/no pools are measured. No sampling has run.
- **Compute:** no inference or Grid5000 allocation has started. Complete all
  relevant data manifests and the live Grid5000 usage-policy check before any
  inference request. No external outreach or allocation was made.

Next, run the tracked source-pool census after Atlas finishes, complete the
missing Wikidata/Wikipedia streams, freeze the EUNIS threshold and available
assignments, obtain the missing WorldCover assignments, and report exact
per-pair yes/no pools before sampling or compute.

The pinned binary classifier is `place-relevance-v2`, run
`bc1fdc83579d6793f44d`, public model revision
`aa9dffe2323b830d3c5da959ad44444482798c76`, based on
`jhu-clsp/mmBERT-small@abc32620dd4f6ab06f5fbe905dc25f310618e09f`. Its pinned
training data is `NoeFlandre/osm-polygon-wikidata-sentence-relevance`, config
`v2-worldwide`, revision `4d0d2b5d53630c24acfb280e9d8159bf6ed0d3fa`, parquet
SHA-256 `0f76f64f9d2a13081ad26cacb27b18f48c204d9150a494dafb340375e82eb270`.
Its historical Wiki-only test had 16,556 examples (accuracy 0.787026, balanced
accuracy 0.782722, positive F1 0.815238, macro-F1 0.781942). These metrics do
not establish Description/Website transfer quality or validate the target
reference cohorts. No model inference or evaluation was run for this census.

The source label-generation prompt is separately pinned at revision
`713ccaf6e15554ef20c241569438dec24868d35c`, SHA-256
`2fb48569c8bbb73fcf584fb7549b34ddd5e4cc365b7a5c75be7429906e312097`. It is
not the binary classifier's inference prompt.

No raw GeoPackage, credentials, environments, caches, sentence text, or
evaluation predictions were added to tracked files. The receipt contains only
counts, pins, hashes, and exclusion totals under git-ignored `data/`.
