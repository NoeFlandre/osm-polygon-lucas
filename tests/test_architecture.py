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
    dynamic_import_helpers = {"__import__", "import_module", "getattr"}
    dynamic_namespace_helpers = {"eval", "exec", "globals", "locals", "vars"}
    imported_dynamic_import_helpers = set(dynamic_import_helpers)
    imported_dynamic_namespace_helpers = set(dynamic_namespace_helpers)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_names.add(alias.asname or "importlib")
                elif alias.name.startswith("importlib."):
                    violations.append("unapproved importlib submodule")
                if alias.name == "builtins":
                    builtins_names.add(alias.asname or "builtins")
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            violations.append("unapproved from-importlib namespace import")
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "importlib.metadata"
            and any(alias.name != "version" for alias in node.names)
        ):
            violations.append("unapproved importlib.metadata API")
        if isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "__builtin__",
        }:
            for alias in node.names:
                if alias.name in dynamic_import_helpers:
                    imported_dynamic_import_helpers.add(alias.asname or alias.name)
                if alias.name in dynamic_namespace_helpers:
                    imported_dynamic_namespace_helpers.add(alias.asname or alias.name)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in imported_dynamic_import_helpers:
            violations.append("unapproved dynamic import function")
        if isinstance(node, ast.Attribute) and node.attr in (
            dynamic_import_helpers - {"import_module"}
        ) | {"__getattribute__", "__getattr__"}:
            violations.append("unapproved dynamic import function")
        if isinstance(node, ast.Name) and node.id in imported_dynamic_namespace_helpers:
            violations.append("unapproved dynamic namespace access")
        if isinstance(node, ast.Attribute) and node.attr in dynamic_namespace_helpers:
            violations.append("unapproved dynamic namespace access")
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            violations.append("unapproved builtins namespace reference")
        if isinstance(node, ast.Attribute) and node.attr == "__builtins__":
            violations.append("unapproved builtins namespace reference")
        if isinstance(node, ast.Attribute) and node.attr == "__globals__":
            violations.append("unapproved function globals access")
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
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            violations.append("module namespace inspection")
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
        "from importlib import import_module\nimport_module('os')": (
            "from-importlib namespace"
        ),
        "from importlib import util as iu\n"
        "spec = iu.find_spec('os')\n"
        "module = iu.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)": "from-importlib namespace",
        "import importlib.util as iu\niu.find_spec('os')": "importlib submodule",
        "from importlib.metadata import entry_points\n"
        "entry_points().select(group='console_scripts')[0].load()": (
            "importlib.metadata API"
        ),
        "import importlib\n"
        "module = importlib.import_module('tools.lucas_description_census')\n"
        "module.__dict__['__builtins__']['__import__']('os')": (
            "module namespace inspection"
        ),
        "import importlib\n"
        "module = importlib.import_module('tools.lucas_description_census')\n"
        "module.census.__globals__['__builtins__']['__import__']('os')": (
            "function globals access"
        ),
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
        "import builtins as bi\nbi.eval(\"__import__('os')\")": (
            "unapproved dynamic namespace access"
        ),
        "from builtins import eval as run\nrun(\"__import__('os')\")": (
            "unapproved dynamic namespace access"
        ),
        "import importlib\nobject.__getattribute__(importlib, 'import_module')('os')": (
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
