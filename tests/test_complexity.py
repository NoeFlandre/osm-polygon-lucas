from pathlib import Path

from radon.complexity import cc_visit
from radon.visitors import Class

ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT_DIR / "src" / "osm_polygon_lucas"
NEW_PRODUCTION_FILES = (
    ROOT_DIR / "tools" / "lucas_description_census.py",
    ROOT_DIR / "tools" / "lucas_direct_source_snapshot.py",
    ROOT_DIR / "tools" / "lucas_point_census_domain.py",
    ROOT_DIR / "tools" / "lucas_point_sampling.py",
    ROOT_DIR / "tools" / "lucas_point_source_census.py",
    ROOT_DIR / "tools" / "lucas_source_pool_census.py",
)
MAX_COMPLEXITY = 5


def function_blocks(source):
    for block in cc_visit(source):
        if isinstance(block, Class):
            yield from block.methods
        else:
            yield block


def test_every_production_function_is_within_max_complexity():
    production_files = [*sorted(PACKAGE_DIR.glob("*.py")), *NEW_PRODUCTION_FILES]
    over = [
        f"{path.name}:{block.lineno} {block.name} CC {block.complexity}"
        for path in production_files
        for block in function_blocks(path.read_text(encoding="utf-8"))
        if block.complexity > MAX_COMPLEXITY and "__mutmut_" not in block.name
    ]

    assert over == []
