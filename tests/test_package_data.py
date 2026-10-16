def test_packaged_source_manifest_matches_repository_manifest(repository_root):
    root = repository_root
    configured = root / "config" / "lucas-2022-source.json"
    packaged = root / "src" / "osm_polygon_lucas" / "lucas_2022_source.json"

    assert packaged.read_bytes() == configured.read_bytes()


def test_dockerignore_allowlists_only_runtime_build_inputs(repository_root):
    lines = (repository_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    patterns = [line for line in lines if line and not line.startswith("#")]

    assert patterns == [
        "*",
        "!README.md",
        "!pyproject.toml",
        "!uv.lock",
        "!MANIFEST.in",
        "!mkdocs.yml",
        "!Dockerfile",
        "!src/",
        "!src/osm_polygon_lucas/",
        "!src/osm_polygon_lucas/*.py",
        "!src/osm_polygon_lucas/*.json",
    ]
