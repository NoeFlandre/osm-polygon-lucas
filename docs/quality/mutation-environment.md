# Mutation-test environment

## Purpose and compatibility issue

The preserved run-7 used the prior mutation setup and remains the overall mutation
record. Its 13 diagnostic survivors were added to the loader tests, but the original
mutmut 3.7 runner ran under Python 3.14 while the project environment used Python
3.12. The runner's LibCST native extension could not be imported by Python 3.12, so a
focused rerun could not start in that mixed environment.

## Locked project environment

- `.python-version` selects Python 3.12; the project interpreter is Python 3.12.11.
- `pyproject.toml` pins the development dependency `mutmut==3.8.0`.
- `uv.lock` was generated with uv 0.11.16 and resolves registry packages from
  `https://pypi.org/simple`.
- The project-local `.venv` was synchronized with `uv sync --locked`. Verified key
  versions are mutmut 3.8.0, LibCST 1.9.0, pytest 9.1.1, Shapely 2.2.0, and NumPy
  2.5.4. The LibCST native module imports from the Python 3.12 environment.
- No global Python installation or prior environment was changed. Temporary package
  caches and mutation workspaces were kept outside the repository under `/tmp`.

## Verification and scope

The complete synthetic suite passed in the pinned environment:

```sh
UV_CACHE_DIR=/tmp/lucas-uv-cache \
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=20261015 \
uv run --locked --no-sync --no-config --python 3.12 \
  python -m pytest -q -p no:cacheprovider
```

Result: `199 passed` on the repeat verification run.

The focused mutation run used a disposable workspace under `/tmp`. Its source,
tests, builder, and `config/` were compared against the project before execution. A
temporary `pyproject.toml` selected the loader module, its tests, and copied `config/`
for manifest tests. The run executed the 13 diagnostic survivors listed in
`mutation-triage.md`; all 13 were killed. Mutmut 3.8.0 generated 749 catalog entries,
but the other entries were not run. The disposable workspace and its logs are local-only
artifacts and are not included in this repository.

The preserved run-7 metadata and log were not overwritten. This report covers code
quality checks only; it contains no LUCAS/OSM benchmark results or source GeoPackage.
