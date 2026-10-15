from pathlib import Path


def test_packaged_source_manifest_matches_repository_manifest():
    root = Path(__file__).resolve().parents[1]
    configured = root / "config" / "lucas-2022-source.json"
    packaged = root / "src" / "osm_polygon_lucas" / "lucas_2022_source.json"

    assert packaged.read_bytes() == configured.read_bytes()
