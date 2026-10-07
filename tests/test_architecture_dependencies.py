import ast
from pathlib import Path


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_platform_adapters_do_not_depend_on_application_services():
    root = Path(__file__).parents[1]
    violations = []
    for path in (root / "platforms").rglob("*.py"):
        for module in _imports(path):
            if module.startswith("services") or module.startswith(
                "astrbot_multi_parser.services"
            ):
                violations.append(str(path.relative_to(root)))
    assert violations == []


def test_core_modules_do_not_depend_on_platform_implementations():
    root = Path(__file__).parents[1]
    violations = []
    for path in (root / "core").rglob("*.py"):
        for module in _imports(path):
            if module.startswith("platforms") or module.startswith(
                "astrbot_multi_parser.platforms"
            ):
                violations.append(str(path.relative_to(root)))
    assert violations == []


def test_simple_platforms_use_base_parser_http_client():
    root = Path(__file__).parents[1]
    simple_platforms = (
        root / "platforms" / "github" / "parser.py",
        root / "platforms" / "pixiv" / "parser.py",
        root / "platforms" / "fanqie" / "parser.py",
        root / "platforms" / "qzone" / "parser.py",
        root / "platforms" / "qqchannel" / "parser.py",
    )
    violations = []
    for path in simple_platforms:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "AsyncClient"
            ):
                violations.append(str(path.relative_to(root)))
    assert violations == []


def test_registered_platforms_have_single_parser_entrypoint():
    from astrbot_multi_parser.platforms.registry import parser_platforms

    root = Path(__file__).parents[1]
    missing = [
        item.key
        for item in parser_platforms()
        if not (root / "platforms" / item.key / "parser.py").is_file()
    ]
    assert missing == []


def test_service_platform_dependencies_are_limited_to_assembly_modules():
    root = Path(__file__).parents[1]
    allowed = {"services/configuration.py", "services/authentication.py"}
    violations = []
    for path in (root / "services").rglob("*.py"):
        if str(path.relative_to(root)) in allowed:
            continue
        for module in _imports(path):
            if module.startswith("platforms") or module.startswith(
                "astrbot_multi_parser.platforms"
            ):
                violations.append(str(path.relative_to(root)))
    assert violations == []
