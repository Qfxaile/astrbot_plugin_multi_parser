"""插件配置的类型化读取工具。"""

from collections.abc import Mapping


class PluginSettings:
    """在不改变配置键的前提下集中处理类型转换和无效值回退。"""

    def __init__(self, values: Mapping[str, object]):
        self.values = values

    def text(self, key: str, default: str = "") -> str:
        value = self.values.get(key, default)
        return str(default if value is None else value).strip()

    def boolean(self, key: str, default: bool = False) -> bool:
        value = self.values.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default

    def integer(self, key: str, default: int = 0, *, minimum: int | None = None) -> int:
        try:
            value = int(self.values.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(value, minimum) if minimum is not None else value

    def decimal(
        self, key: str, default: float = 0.0, *, minimum: float | None = None
    ) -> float:
        try:
            value = float(self.values.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(value, minimum) if minimum is not None else value

    def choice(self, key: str, options: set[str], default: str) -> str:
        value = self.text(key, default).lower()
        return value if value in options else default

    def platform_enabled(self, name: str, default: bool = True) -> bool:
        return self._platform_switch("platform_switches", name, default)

    def platform_proxy_enabled(self, name: str, default: bool = False) -> bool:
        """返回指定平台是否启用代理。"""
        return self._platform_switch("proxy_switches", name, default)

    def _platform_switch(self, key: str, name: str, default: bool) -> bool:
        switches = self.values.get(key)
        if not isinstance(switches, Mapping):
            return default
        value = switches.get(name, default)
        return value if isinstance(value, bool) else default
