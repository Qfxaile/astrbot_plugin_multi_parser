import sys
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN_NAME = "astrbot_multi_parser"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# AstrBot 按 metadata.yaml 中的名称加载插件；测试不应依赖克隆目录同名。
if PLUGIN_NAME not in sys.modules:
    plugin_package = ModuleType(PLUGIN_NAME)
    plugin_package.__path__ = [str(PLUGIN_ROOT)]
    sys.modules[PLUGIN_NAME] = plugin_package


@pytest.fixture
def assert_temporary_image():
    created_paths: set[Path] = set()

    def assert_image(result, value: str, expected_bytes: bytes) -> Path:
        image_path = Path(value)
        assert image_path in result.temporary_files
        assert image_path.is_file()
        assert image_path.read_bytes() == expected_bytes
        assert not value.startswith("base64://")
        created_paths.add(image_path)
        return image_path

    yield assert_image

    for image_path in created_paths:
        image_path.unlink(missing_ok=True)
