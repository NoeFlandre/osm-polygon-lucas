import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "osm_polygon_lucas"
ALLOWED_STDLIB = {
    "__future__",
    "argparse",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "importlib",
    "json",
    "pathlib",
    "sys",
    "tempfile",
    "math",
    "numbers",
    "types",
    "typing",
}
EXTRA_STDLIB = {"lucas_gpkg": {"contextlib", "pathlib", "sqlite3"}}
ALLOWED_THIRD_PARTY = {"shapely"}
ALLOWED_INTERNAL = {
    "__init__": {
        "lucas_gpkg",
        "lucas_taxonomy",
        "polygon_coverage",
        "support_eligibility",
    },
    "__main__": {"cli"},
    "cli": {"lucas_gpkg", "lucas_taxonomy", "polygon_coverage", "support_eligibility"},
    "lucas_gpkg": {"support_eligibility"},
    "lucas_taxonomy": {"polygon_coverage"},
    "polygon_coverage": {"support_eligibility"},
    "support_eligibility": set(),
}


def imported_names(module_path: Path) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    return names


def violations_in(module_path: Path) -> list[str]:
    module = module_path.stem
    allowed_stdlib = ALLOWED_STDLIB | EXTRA_STDLIB.get(module, set())
    found: list[str] = []
    for name in imported_names(module_path):
        top, _, rest = name.partition(".")
        if top in allowed_stdlib or top in ALLOWED_THIRD_PARTY:
            continue
        if (
            top == "osm_polygon_lucas"
            and rest.split(".")[0] in ALLOWED_INTERNAL[module]
        ):
            continue
        found.append(f"{module} imports {name}")
    return found


def test_package_modules_import_only_their_allowed_dependencies():
    module_paths = sorted(PACKAGE_DIR.glob("*.py"))
    assert {path.stem for path in module_paths} == set(ALLOWED_INTERNAL)

    violations = [
        violation for path in module_paths for violation in violations_in(path)
    ]

    assert violations == []
