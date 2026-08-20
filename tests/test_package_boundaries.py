import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

PLUGIN_NAME = "astrbot_multi_parser"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_platform_login_contract_uses_explicit_core_module():
    module = import_module("astrbot_multi_parser.core.platform_login")

    assert module.PlatformLoginProvider.__module__.endswith("core.platform_login")


def test_platform_registry_is_owned_by_platform_package():
    module = import_module("astrbot_multi_parser.platforms.registry")

    assert len(module.PLATFORM_REGISTRY) == 13


@pytest.mark.parametrize(
    ("package_name", "implementation_module"),
    [
        ("astrbot_multi_parser.core", "astrbot_multi_parser.core.parser"),
        (
            "astrbot_multi_parser.services",
            "astrbot_multi_parser.services.authentication",
        ),
        ("astrbot_multi_parser.platforms", "astrbot_multi_parser.platforms.zhihu"),
    ],
)
def test_package_initializers_do_not_eagerly_import_implementation_modules(
    package_name,
    implementation_module,
):
    script = (
        "import sys; from types import ModuleType; "
        f"package = ModuleType({PLUGIN_NAME!r}); "
        f"package.__path__ = {[str(PLUGIN_ROOT)]!r}; "
        f"sys.modules[{PLUGIN_NAME!r}] = package; "
        f"import {package_name}; "
        f"print({implementation_module!r} in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        cwd=PLUGIN_ROOT.parent,
        text=True,
    )

    assert completed.stdout.strip() == "False"


@pytest.mark.parametrize(
    ("package_name", "export_name", "source_module"),
    [
        (
            "astrbot_multi_parser.core",
            "ParseResult",
            "astrbot_multi_parser.core.contracts",
        ),
        (
            "astrbot_multi_parser.services",
            "AuthenticationService",
            "astrbot_multi_parser.services.authentication",
        ),
        (
            "astrbot_multi_parser.platforms",
            "ZhihuParser",
            "astrbot_multi_parser.platforms.zhihu",
        ),
        (
            "astrbot_multi_parser.platforms",
            "QQChannelParser",
            "astrbot_multi_parser.platforms.qqchannel",
        ),
        (
            "astrbot_multi_parser.platforms",
            "QzoneParser",
            "astrbot_multi_parser.platforms.qzone",
        ),
    ],
)
def test_package_public_exports_remain_available_lazily(
    package_name,
    export_name,
    source_module,
):
    package = import_module(package_name)
    source = import_module(source_module)

    assert getattr(package, export_name) is getattr(source, export_name)
