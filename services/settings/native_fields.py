"""将现有插件配置映射到 WebUI 菜单，并校验允许提交的字段。"""

import json
import math
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from ...core.settings import PluginSettings

NATIVE_GROUPS = {
    "delivery": (
        (
            "合并转发与消息上限",
            (
                "forward_mode",
                "forward_image_threshold",
                "forward_text_threshold",
                "non_forward_message_limit",
                "limit_private_chat_messages",
            ),
        ),
        (
            "视频发送与回退",
            (
                "send_video_by_url",
                "max_video_size_mb",
                "video_over_limit_action",
                "allow_unknown_video_size",
            ),
        ),
        (
            "消息内容与回应",
            (
                "filter_output_links",
                "filtered_link_text",
                "enable_parse_reaction",
            ),
        ),
    ),
    "ai": (
        ("分享读取", ("parse_mode",)),
        ("会话记录", ("enable_conversation_history", "conversation_history_mode")),
        (
            "AI 总结",
            (
                "enable_ai_summary",
                "ai_summary_mode",
                "ai_summary_text_provider_id",
                "ai_summary_vision_provider_id",
                "ai_summary_prompt",
                "ai_summary_timeout_seconds",
                "ai_summary_max_input_chars",
                "ai_summary_max_images",
            ),
        ),
    ),
    "network": (
        (
            "请求与下载",
            (
                "request_timeout_seconds",
                "size_check_timeout_seconds",
                "image_download_concurrency",
            ),
        ),
        ("代理地址", ("proxy_url",)),
    ),
}

MINIMUMS = {
    "forward_image_threshold": 0,
    "forward_text_threshold": 0,
    "non_forward_message_limit": -1,
    "request_timeout_seconds": 1,
    "size_check_timeout_seconds": 1,
    "image_download_concurrency": 1,
    "ai_summary_timeout_seconds": 1,
    "ai_summary_max_input_chars": 1000,
    "ai_summary_max_images": 0,
}
MAXIMUMS = {"image_download_concurrency": 16}
TEXT_LIMITS = {
    "ai_summary_prompt": 32768,
    "proxy_url": 4096,
    "filtered_link_text": 4096,
}


class SettingsValidationError(ValueError):
    """表示 WebUI 配置提交未通过字段白名单或值检查。"""


@cache
def _field_schema() -> dict[str, dict]:
    schema_path = Path(__file__).resolve().parents[2] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return {
        key: schema[key]
        for groups in NATIVE_GROUPS.values()
        for _, keys in groups
        for key in keys
    }


def _value(settings: PluginSettings, key: str, field: dict):
    default = field["default"]
    if key == "ai_summary_prompt":
        value = settings.values.get(key)
        return value if isinstance(value, str) else default
    if field["type"] == "bool":
        return settings.boolean(key, default)
    if field["type"] in {"int", "float"}:
        reader = settings.integer if field["type"] == "int" else settings.decimal
        try:
            number = reader(key, default)
            return number if math.isfinite(number) else default
        except OverflowError:
            return default
    if options := field.get("options"):
        return settings.choice(key, set(options), default)
    return settings.text(key, default)


def native_groups(config: Mapping[str, object]) -> dict[str, list[dict]]:
    """返回明确允许展示的配置及其当前值。"""
    settings = PluginSettings(config)
    fields = _field_schema()
    return {
        group: [
            {
                "title": title,
                "fields": [
                    {
                        "key": key,
                        "label": fields[key]["description"],
                        "hint": fields[key].get("hint", ""),
                        "type": "provider"
                        if fields[key].get("_special") == "select_provider"
                        else fields[key]["type"],
                        "value": _value(settings, key, fields[key]),
                        "options": fields[key].get("options", []),
                        "labels": fields[key].get("labels", []),
                        "min": MINIMUMS.get(key),
                        "max": MAXIMUMS.get(key),
                    }
                    for key in keys
                ],
            }
            for title, keys in groups
        ]
        for group, groups in NATIVE_GROUPS.items()
    }


def validate_native_settings(value: object) -> dict[str, object]:
    """校验菜单部分更新，保留原生配置中的其余字段。"""
    fields = _field_schema()
    if not isinstance(value, Mapping) or any(key not in fields for key in value):
        raise SettingsValidationError("设置包含未开放的字段")
    result = {}
    for key, item in value.items():
        field = fields[key]
        kind = field["type"]
        if kind == "bool":
            valid = type(item) is bool
        elif kind in {"int", "float"}:
            types = (int,) if kind == "int" else (int, float)
            valid = (
                type(item) in types
                and -1e15 <= item <= 1e15
                and MINIMUMS.get(key, -math.inf) <= item <= MAXIMUMS.get(key, math.inf)
            )
        elif options := field.get("options"):
            valid = isinstance(item, str) and item in options
        else:
            valid = (
                isinstance(item, str)
                and len(item) <= TEXT_LIMITS.get(key, 512)
                and "\x00" not in item
                and (key == "ai_summary_prompt" or not any(c in item for c in "\r\n"))
            )
        if not valid:
            raise SettingsValidationError(
                f"{field['description']}的值无效或超出允许范围"
            )
        result[key] = (
            item.strip()
            if isinstance(item, str) and key != "ai_summary_prompt"
            else item
        )
    return result
