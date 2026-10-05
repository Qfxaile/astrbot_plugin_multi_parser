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
