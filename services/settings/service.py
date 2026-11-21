"""维护插件 WebUI 的解析类型策略及配置视图。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from copy import deepcopy

from astrbot.api import logger

from ...core.http import (
    ProxyConfigurationError,
    cookie_config_value,
    validate_proxy_url,
)
from ...core.settings import PluginSettings
from ...platforms.registry import PLATFORM_REGISTRY
from .access_rules import AccessRules
from .native_fields import (
    SettingsValidationError,
    native_groups,
    validate_native_settings,
)
from .runtime_config import RuntimeSettingsConfig

FEATURES_KEY = "webui_parse_type_settings_v1"
MAX_COOKIE_LENGTH = 8192
MAX_GITHUB_TOKEN_LENGTH = 512


class PluginSettingsService:
    """提供解析设置的读取、校验、持久化和运行时即时更新。"""

    def __init__(
        self,
        config: MutableMapping[str, object],
        runtime_config: RuntimeSettingsConfig,
        *,
        read_store: Callable[[str, object], Awaitable[object]],
        write_store: Callable[[str, object], Awaitable[None]],
    ) -> None:
        self.config = config
        self.runtime_config = runtime_config
        self.read_store = read_store
        self.write_store = write_store
        self._lock = asyncio.Lock()
        self._revision = 0
        self._parse_types = self._defaults()
        self.access_rules = AccessRules()

    async def load(self) -> None:
        stored = await self.read_store(FEATURES_KEY, {})
        if not isinstance(stored, Mapping):
            return
        self._parse_types = self._merge_valid_features(stored, self._parse_types)
        try:
            self.access_rules = AccessRules.from_store(stored.get("access_rules", {}))
        except SettingsValidationError:
            logger.warning("WebUI 名单读取失败，已载入其余设置。")
        revision = stored.get("revision", 0)
        self._revision = revision if type(revision) is int and revision >= 0 else 0
        self.runtime_config.replace_parse_types(self._parse_types)

    async def read_snapshot(self) -> dict[str, object]:
        """等保存或回滚结束后，再读取同一版本的配置和版本号。"""
        async with self._lock:
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        settings = PluginSettings(self.config)
        platforms = []
        for registration in PLATFORM_REGISTRY:
            cookie_key = registration.cookie_config_key
            platforms.append(
                {
                    "key": registration.key,
                    "name": registration.display_name,
                    "enabled": settings.platform_enabled(
                        registration.key, registration.enabled_by_default
                    ),
                    "proxy_enabled": settings.platform_proxy_enabled(registration.key),
                    "parse_type_controls_ready": registration.parse_type_controls_ready,
                    "parse_types": [
                        {
                            "key": item.key,
                            "name": item.display_name,
                            "enabled": self._parse_types[registration.key][item.key],
                        }
                        for item in registration.parse_types
                    ],
                    "cookie": {
                        "supported": cookie_key is not None,
                        "value": cookie_config_value(self.config, cookie_key)
                        if cookie_key
                        else "",
                    },
                }
            )
            if registration.key == "github":
                platforms[-1]["token"] = {
                    "value": settings.text("github_token"),
                }
        return {
            "revision": self._revision,
            "scope": "当前 AstrBot 实例",
            "platforms": platforms,
            "groups": native_groups(self.config),
            "access_rules": deepcopy(self.access_rules.data),
        }

    async def update(self, payload: object) -> dict[str, object]:
        """保存并发布设置，已开始的写入在请求取消后仍由配置锁保护。"""
        operation = asyncio.create_task(self._update(payload))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            await asyncio.gather(operation, return_exceptions=True)
            raise

    async def _update(self, payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise SettingsValidationError("请求内容格式无效")
        allowed = {
            "revision",
            "platform_switches",
            "parse_types",
            "cookies",
            "clear_cookies",
            "github_token",
            "settings",
            "proxy_switches",
            "access_rules",
        }
        if set(payload) - allowed:
            raise SettingsValidationError("请求包含不支持的配置字段")
        expected_revision = payload.get("revision")
        if type(expected_revision) is not int:
            raise SettingsValidationError("缺少有效的配置版本")

        async with self._lock:
            if expected_revision != self._revision:
                raise SettingsValidationError("配置已被其他页面更新，请重新加载")
            next_access = (
                AccessRules.from_payload(payload["access_rules"])
                if "access_rules" in payload
                else self.access_rules
            )
            new_platforms = self._validate_switches(
                payload.get("platform_switches", {})
            )
            new_features = self._validate_features(payload.get("parse_types", {}))
            new_cookies = self._validate_cookies(payload.get("cookies", {}))
            clear_cookies = self._validate_cookie_clears(
                payload.get("clear_cookies", [])
            )
            if set(new_cookies) & set(clear_cookies):
                raise SettingsValidationError("同一平台不能同时替换和清除 Cookies")

            native_changes = validate_native_settings(payload.get("settings", {}))
            self._apply_proxy_changes(native_changes, payload.get("proxy_switches", {}))
            if "github_token" in payload:
                native_changes["github_token"] = self._validate_github_token(
                    payload["github_token"]
                )
            if new_platforms:
                current = self.config.get("platform_switches")
                switches = dict(current) if isinstance(current, Mapping) else {}
                switches.update(new_platforms)
                native_changes["platform_switches"] = switches
            if new_cookies or clear_cookies:
                current = self.config.get("cookies")
                cookies = dict(current) if isinstance(current, Mapping) else {}
                for item in PLATFORM_REGISTRY:
                    if item.key in new_cookies:
                        cookies[item.cookie_config_key] = new_cookies[item.key]
                    elif item.key in clear_cookies:
                        cookies[item.cookie_config_key] = ""
                native_changes["cookies"] = cookies

            old_values = {
                key: self.config[key] for key in native_changes if key in self.config
            }
            next_features = deepcopy(self._parse_types)
            for platform, changes in new_features.items():
                next_features[platform].update(changes)
            next_revision = self._revision + 1

            try:
                self.config.update(native_changes)
                if native_changes:
                    await self._save_native_config()
                await self.write_store(
                    FEATURES_KEY,
                    {
                        "revision": next_revision,
                        "platforms": next_features,
                        "access_rules": next_access.data,
                    },
                )
            except Exception:
                self.config.update(old_values)
                for key in native_changes.keys() - old_values.keys():
                    self.config.pop(key, None)
                if native_changes:
                    try:
                        await self._save_native_config()
                    except Exception:
                        pass
                raise

            self._parse_types = next_features
            self.access_rules = next_access
            self._revision = next_revision
            self.runtime_config.replace_parse_types(next_features)
            return self.snapshot()

    async def _save_native_config(self) -> None:
        save_async = getattr(self.config, "save_config_async", None)
        if callable(save_async):
            await save_async()
            return
        save_sync = getattr(self.config, "save_config", None)
        if callable(save_sync):
            await asyncio.to_thread(save_sync)

    def _apply_proxy_changes(self, changes: dict[str, object], payload: object) -> None:
        updates = self._validate_switches(payload)
        if not updates and "proxy_url" not in changes:
            return
        current = self.config.get("proxy_switches")
        switches = dict(current) if isinstance(current, Mapping) else {}
        switches.update(updates)
        if updates:
            changes["proxy_switches"] = switches
        current_url = PluginSettings(self.config).text("proxy_url")
        new_url = changes.get("proxy_url", current_url)
        try:
            if any(updates.values()) or ("proxy_url" in changes and new_url):
                validate_proxy_url(new_url)
        except ProxyConfigurationError as exc:
            raise SettingsValidationError(str(exc)) from None
        if "proxy_url" in changes and not new_url:
            settings = PluginSettings({"proxy_switches": switches})
            if any(
                settings.platform_proxy_enabled(item.key) for item in PLATFORM_REGISTRY
            ):
                raise SettingsValidationError("请先关闭所有平台代理，再清空代理地址")

    @staticmethod
    def _validate_github_token(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) > MAX_GITHUB_TOKEN_LENGTH
            or any(not "!" <= character <= "~" for character in value)
        ):
            raise SettingsValidationError("GitHub Token 格式无效或长度超限")
        return value

    @staticmethod
    def _defaults() -> dict[str, dict[str, bool]]:
        return {
            item.key: {feature.key: True for feature in item.parse_types}
            for item in PLATFORM_REGISTRY
        }

    def _validate_switches(self, value: object) -> dict[str, bool]:
        if not isinstance(value, Mapping):
            raise SettingsValidationError("平台开关格式无效")
        registered = {item.key for item in PLATFORM_REGISTRY}
        result = {}
        for key, enabled in value.items():
            if key not in registered or type(enabled) is not bool:
                raise SettingsValidationError("平台开关包含无效字段")
            result[key] = enabled
        return result

    def _validate_features(self, value: object) -> dict[str, dict[str, bool]]:
        if not isinstance(value, Mapping):
            raise SettingsValidationError("解析类型设置格式无效")
        registrations = {item.key: item for item in PLATFORM_REGISTRY}
        result: dict[str, dict[str, bool]] = {}
        for platform, features in value.items():
            registration = registrations.get(platform)
            if registration is None or not isinstance(features, Mapping):
                raise SettingsValidationError("解析类型包含无效平台")
            allowed = {item.key for item in registration.parse_types}
            if any(
                key not in allowed or type(enabled) is not bool
                for key, enabled in features.items()
            ):
                raise SettingsValidationError("解析类型包含无效字段")
            result[platform] = dict(features)
        return result

    def _validate_cookies(self, value: object) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise SettingsValidationError("Cookie 设置格式无效")
        registrations = {
            item.key: item for item in PLATFORM_REGISTRY if item.cookie_config_key
        }
        result = {}
        for platform, cookie in value.items():
            registration = registrations.get(platform)
            if registration is None or not isinstance(cookie, str):
                raise SettingsValidationError("Cookie 设置包含无效字段")
            if not cookie:
                continue
            if len(cookie) > MAX_COOKIE_LENGTH or any(
                character in cookie for character in "\r\n\x00"
            ):
                raise SettingsValidationError("Cookie 内容格式无效或长度超限")
            result[platform] = cookie
        return result

    @staticmethod
    def _validate_cookie_clears(value: object) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise SettingsValidationError("Cookie 清除列表格式无效")
        supported = {item.key for item in PLATFORM_REGISTRY if item.cookie_config_key}
        if len(value) != len(set(value)) or any(
            item not in supported for item in value
        ):
            raise SettingsValidationError("Cookie 清除列表包含无效平台")
        return value

    def _merge_valid_features(
        self,
        stored: Mapping[str, object],
        defaults: dict[str, dict[str, bool]],
    ) -> dict[str, dict[str, bool]]:
        raw_platforms = stored.get("platforms")
        if not isinstance(raw_platforms, Mapping):
            return defaults
        merged = deepcopy(defaults)
        try:
            for platform, features in self._validate_features(raw_platforms).items():
                merged[platform].update(features)
        except SettingsValidationError:
            return defaults
        return merged
