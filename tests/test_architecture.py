import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "osm_polygon_lucas"
ROOT_DIR = Path(__file__).resolve().parents[1]
TOOL_MODULES = {
    "lucas_description_census": {
        "__future__",
        "argparse",
        "collections",
        "dataclasses",
        "hashlib",
        "importlib",
        "json",
        "osm_polygon_lucas",
        "pathlib",
        "pyarrow.parquet",
        "pyproj",
        "shapely",
        "shapely.errors",
        "shapely.geometry",
        "shapely.geometry.base",
        "shapely.strtree",
        "typing",
    },
    "lucas_direct_source_snapshot": {
        "__future__",
        "collections",
        "collections.abc",
        "hashlib",
        "importlib",
        "json",
        "math",
        "os",
        "pathlib",
        "pyarrow.parquet",
        "shapely",
        "shapely.errors",
        "shapely.geometry",
        "shapely.geometry.base",
        "tools",
        "typing",
        "uuid",
    },
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
    "lucas_point_sampling": {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "h3",
        "hashlib",
        "importlib.metadata",
        "math",
        "types",
        "typing",
    },
    "lucas_source_pool_census": {
        "__future__",
        "argparse",
        "collections",
        "collections.abc",
        "hashlib",
        "importlib",
        "json",
        "math",
        "pathlib",
        "pyarrow.parquet",
        "pyproj",
        "re",
        "shapely",
        "shapely.errors",
        "shapely.geometry",
        "shapely.geometry.base",
        "tools.lucas_description_census",
        "tools.lucas_direct_source_snapshot",
        "typing",
    },
}
ALLOWED_DYNAMIC_IMPORTS = {
    "lucas_description_census": {"pyarrow", "pyproj"},
    "lucas_direct_source_snapshot": {
        "pyarrow",
        "pyproj",
        "pyproj.database",
        "tools.lucas_description_census",
    },
    "lucas_point_census_domain": set(),
    "lucas_point_source_census": {
        "pyarrow",
        "pyproj",
        "pyproj.database",
        "tools.lucas_direct_source_snapshot",
    },
    "lucas_point_sampling": set(),
    "lucas_source_pool_census": {"pyarrow", "pyproj"},
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
    importlib_names = {"importlib"}
    builtins_names = {"builtins", "__builtins__"}
    getattr_names = {"getattr"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_names.add(alias.asname or "importlib")
                if alias.name == "builtins":
                    builtins_names.add(alias.asname or "builtins")
        if isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "__builtin__",
        }:
            for alias in node.names:
                if alias.name == "getattr":
                    getattr_names.add(alias.asname or "getattr")
    parents = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            if any(alias.name == "import_module" for alias in node.names):
                violations.append("from-importlib import_module alias")
        if isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "__builtin__",
        }:
            if any(alias.name == "__import__" for alias in node.names):
                violations.append("from-builtins __import__ alias")
        if isinstance(node, ast.Name) and node.id in {"__import__", "import_module"}:
            violations.append("unapproved dynamic import function")
        if isinstance(node, ast.Attribute) and node.attr in {
            "__import__",
            "__getattribute__",
            "__getattr__",
        }:
            violations.append("unapproved dynamic import function")
        if isinstance(node, ast.Name) and node.id in getattr_names:
            violations.append("unapproved dynamic import function")
        if isinstance(node, ast.Attribute) and node.attr == "getattr":
            violations.append("unapproved dynamic import function")
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            violations.append("unapproved builtins namespace reference")
        if isinstance(node, ast.Name) and node.id in importlib_names:
            import_attribute = parents.get(node)
            direct_call = (
                parents.get(import_attribute)
                if isinstance(import_attribute, ast.Attribute)
                else None
            )
            if not (
                isinstance(import_attribute, ast.Attribute)
                and import_attribute.attr == "import_module"
                and import_attribute.value is node
                and isinstance(direct_call, ast.Call)
                and direct_call.func is import_attribute
            ):
                violations.append("unapproved importlib namespace reference")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            helper = node.func.id
            if helper in {"globals", "locals", "eval", "exec"}:
                violations.append("unapproved dynamic namespace access")
            elif helper == "vars":
                if not node.args or (
                    isinstance(node.args[0], ast.Name)
                    and node.args[0].id in importlib_names | builtins_names
                ):
                    violations.append("namespace inspection on an import module")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "__dict__"
            and isinstance(node.value, ast.Name)
            and node.value.id in importlib_names | builtins_names
        ):
            violations.append("namespace inspection on an import module")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in importlib_names | builtins_names
        ):
            violations.append("dynamic indexing into an import namespace")
        if isinstance(node, ast.Attribute) and node.attr == "import_module":
            parent = parents.get(node)
            if not (
                isinstance(node.value, ast.Name)
                and node.value.id == "importlib"
                and isinstance(parent, ast.Call)
                and parent.func is node
            ):
                violations.append("unapproved import_module reference")
            elif isinstance(parent, ast.Call) and not (
                parent.args
                and isinstance(parent.args[0], ast.Constant)
                and isinstance(parent.args[0].value, str)
            ):
                violations.append("dynamic import target must be a string literal")
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
    ] + violations


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
        module: ROOT_DIR / "tools" / f"{module}.py" for module in TOOL_MODULES
    }
    assert all(path.is_file() for path in tool_paths.values())
    violations = [
        f"{module} imports {name}"
        for module, path in tool_paths.items()
        for name in imported_names(path)
        if name not in TOOL_MODULES[module]
    ]
    assert violations == []


def test_runner_dynamic_imports_are_explicit_and_local_or_runtime_only():
    assert set(ALLOWED_DYNAMIC_IMPORTS) == set(TOOL_MODULES)
    assert all(
        set(dynamic_imports(ROOT_DIR / "tools" / f"{module}.py")) == allowed
        for module, allowed in ALLOWED_DYNAMIC_IMPORTS.items()
    )


def test_dynamic_import_allowlist_rejects_alias_and_namespace_bypasses(tmp_path):
    examples = {
        "from importlib import import_module\nimport_module('os')": "from-importlib",
        "import importlib as il\nil.import_module('os')": "unapproved import_module",
        "__import__('os')": "unapproved dynamic import",
        "import importlib\nimportlib.import_module(target)": "string literal",
        "import importlib\n"
        "getattr(importlib, 'import_module')('os')": "unapproved dynamic import",
        "getattr(__builtins__, '__import__')('os')": "unapproved dynamic import",
        "import builtins as bi\n"
        "bi.__dict__['__import__']('os')": "namespace inspection",
        "import importlib\n"
        "import builtins as b\n"
        "b.getattr(importlib, 'import_module')('os')": "unapproved dynamic import",
        "from builtins import getattr as g\n"
        "import importlib\n"
        "g(importlib, 'import_module')('os')": "unapproved dynamic import",
        "import importlib\n"
        "object.__getattribute__(importlib, 'import_module')('os')": (
            "unapproved dynamic import"
        ),
    }
    for index, (source, expected_violation) in enumerate(examples.items()):
        module_path = tmp_path / f"dynamic_import_{index}.py"
        module_path.write_text(source, encoding="utf-8")
        assert any(expected_violation in item for item in dynamic_imports(module_path))


def test_domain_transformations_do_not_perform_filesystem_io():
    domain_paths = (
        ROOT_DIR / "tools" / "lucas_point_census_domain.py",
        ROOT_DIR / "tools" / "lucas_point_sampling.py",
    )
    assert all(domain_io_calls(path) == [] for path in domain_paths)
