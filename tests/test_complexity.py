from pathlib import Path

from radon.complexity import cc_visit
from radon.visitors import Class

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "osm_polygon_lucas"
MAX_COMPLEXITY = 5


def function_blocks(source):
    for block in cc_visit(source):
        if isinstance(block, Class):
            yield from block.methods
        else:
            yield block


def test_every_production_function_is_within_max_complexity():
    over = [
        f"{path.name}:{block.lineno} {block.name} CC {block.complexity}"
        for path in sorted(PACKAGE_DIR.glob("*.py"))
        for block in function_blocks(path.read_text(encoding="utf-8"))
        if block.complexity > MAX_COMPLEXITY and "__mutmut_" not in block.name
    ]

    assert over == []
