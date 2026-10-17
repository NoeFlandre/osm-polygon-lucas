import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "osm_polygon_lucas"
ROOT_DIR = Path(__file__).resolve().parents[1]
TOOL_MODULES = {
    "lucas_point_census_domain": {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "hashlib",
        "json",
        "math",
        "typing",
        "shapely",
        "shapely.geometry",
        "shapely.geometry.base",
        "osm_polygon_lucas.point_coverage",
        "tools.lucas_point_sampling",
    },
    "lucas_point_source_census": {
        "__future__",
        "argparse",
        "collections",
        "collections.abc",
        "dataclasses",
        "importlib",
        "json",
        "os",
        "platform",
        "pathlib",
        "typing",
        "uuid",
        "shapely",
        "shapely.errors",
        "shapely.geometry.base",
        "osm_polygon_lucas.lucas_point_gpkg",
        "osm_polygon_lucas.point_coverage",
        "tools",
        "tools.lucas_direct_source_snapshot",
        "tools.lucas_point_census_domain",
        "tools.lucas_point_sampling",
        "tools.lucas_source_pool_census",
    },
}
ALLOWED_DYNAMIC_IMPORTS = {
    "lucas_point_source_census": {
        "pyarrow",
        "pyproj",
        "pyproj.database",
        "tools.lucas_direct_source_snapshot",
    }
}
DOMAIN_IO_CALLS = {
    "exists",
    "is_file",
    "link",
    "mkdir",
    "open",
    "read_text",
    "stat",
    "unlink",
    "write_text",
}
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
EXTRA_STDLIB = {
    "lucas_gpkg": {"contextlib", "pathlib", "sqlite3"},
    "lucas_point_gpkg": {"contextlib", "pathlib", "sqlite3"},
}
ALLOWED_THIRD_PARTY = {"shapely"}
ALLOWED_INTERNAL = {
    "__init__": {
        "lucas_gpkg",
        "lucas_point_gpkg",
        "lucas_taxonomy",
        "point_coverage",
        "polygon_coverage",
        "support_eligibility",
    },
    "__main__": {"cli"},
    "cli": {"lucas_gpkg", "lucas_taxonomy", "polygon_coverage", "support_eligibility"},
    "lucas_gpkg": {"support_eligibility"},
    "lucas_point_gpkg": {"lucas_gpkg", "point_coverage"},
    "lucas_taxonomy": {"polygon_coverage"},
    "polygon_coverage": {"support_eligibility"},
    "point_coverage": {"lucas_taxonomy", "polygon_coverage", "support_eligibility"},
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


def dynamic_imports(module_path: Path) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def domain_io_calls(module_path: Path) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return [
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id in DOMAIN_IO_CALLS
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in DOMAIN_IO_CALLS
        )
    ]


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


def test_runner_and_domain_modules_import_only_allowed_dependencies():
    tool_paths = {
        path.stem: path
        for path in (ROOT_DIR / "tools").glob("lucas_point_*.py")
        if path.stem in TOOL_MODULES
    }
    assert set(tool_paths) == set(TOOL_MODULES)
    violations = [
        f"{module} imports {name}"
        for module, path in tool_paths.items()
        for name in imported_names(path)
        if name not in TOOL_MODULES[module]
    ]
    assert violations == []


def test_runner_dynamic_imports_are_explicit_and_local_or_runtime_only():
    tool_path = ROOT_DIR / "tools" / "lucas_point_source_census.py"
    assert set(dynamic_imports(tool_path)) == ALLOWED_DYNAMIC_IMPORTS[tool_path.stem]


def test_domain_transformations_do_not_perform_filesystem_io():
    domain_path = ROOT_DIR / "tools" / "lucas_point_census_domain.py"
    assert domain_io_calls(domain_path) == []
