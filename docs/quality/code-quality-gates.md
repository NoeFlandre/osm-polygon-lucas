# Code quality gates

The numeric complexity baseline is intentionally scoped to the installable
`src/osm_polygon_lucas` package and the point-census domain and runner modules:
`tools/lucas_point_census_domain.py` and
`tools/lucas_point_source_census.py`. Every function in this scope must have
cyclomatic complexity at most 5, and every measured line and branch CRAP score
must remain strictly below 6. The thresholds and selected files match the
established baseline.

The following census tools are outside those numeric metrics:
`lucas_description_census.py`, `lucas_direct_source_snapshot.py`,
`lucas_point_sampling.py`, and `lucas_source_pool_census.py`. Their import
boundaries and dynamic imports are checked by `tests/test_architecture.py`, and
their code is included in the full hosted mutation run. Passing the scoped
numeric check must not be read as a CRAP or complexity result for these files.

Hosted QA also runs the complete synthetic test suite, Ruff lint and format,
`ty`, a strict MkDocs build, package and CLI smoke checks, and a Docker build
and container CLI smoke check. Mutation outcomes are validated and every
surviving mutant must have a matching triage entry. The workflow keeps mutation
outputs inside CI and does not upload a report artifact.
