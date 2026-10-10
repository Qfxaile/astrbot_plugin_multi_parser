"""平台 Cookie 配置的读取、保存和失败回滚。"""

from collections.abc import MutableMapping

from ...core.http import cookie_config_value, set_cookie_config_value
from ...core.platform_login import PlatformLoginError


class CookieStore:
    """封装 AstrBot 配置对象的 Cookie 持久化边界。"""

    def __init__(self, config: MutableMapping[str, object]):
        self.config = config

    def get(self, key: str) -> object:
        return cookie_config_value(self.config, key)

    def save(self, key: str, value: str) -> None:
        previous_value = self.get(key)
        set_cookie_config_value(self.config, key, value)
        save_config = getattr(self.config, "save_config", None)
        if not callable(save_config):
            return
        try:
            save_config()
        except Exception as exc:
            set_cookie_config_value(self.config, key, str(previous_value or ""))
            raise PlatformLoginError("Cookies 保存失败，原配置未被修改。") from exc
