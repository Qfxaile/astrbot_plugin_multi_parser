"""向解析器提供与原生配置隔离的 WebUI 解析类型设置。"""

from collections.abc import Iterator, Mapping


class RuntimeSettingsConfig(Mapping[str, object]):
    """只覆盖解析类型视图，不把 WebUI 设置写入 AstrBotConfig。"""

    def __init__(self, source: Mapping[str, object]) -> None:
        self._source = source
        self._parse_types: dict[str, dict[str, bool]] = {}

    def replace_parse_types(self, value: Mapping[str, Mapping[str, bool]]) -> None:
        self._parse_types = {
            str(platform): dict(features) for platform, features in value.items()
        }

    def __getitem__(self, key: str) -> object:
        if key == "platform_feature_switches":
            return self._parse_types
        return self._source[key]

    def __iter__(self) -> Iterator[str]:
        yield from self._source
        if "platform_feature_switches" not in self._source:
            yield "platform_feature_switches"

    def __len__(self) -> int:
        return len(self._source) + int("platform_feature_switches" not in self._source)
