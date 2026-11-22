"""按一个全局名单模式计算群聊与统一私聊的解析权限。"""

import re
from dataclasses import dataclass, field

from astrbot.core.platform.message_type import MessageType

from ...core.settings.values import restrict_parse_types
from ...platforms.registry import PLATFORM_REGISTRY
from ..event_identity import EventIdentity
from .native_fields import SettingsValidationError

PLATFORM_KEYS = frozenset(spec.key for spec in PLATFORM_REGISTRY)
PLATFORM_TYPE_KEYS = {
    spec.key: frozenset(f"{spec.key}/{item.key}" for item in spec.parse_types)
    for spec in PLATFORM_REGISTRY
    if spec.parse_type_controls_ready
}
TYPE_KEYS = frozenset().union(*PLATFORM_TYPE_KEYS.values())
GROUP_MESSAGE = MessageType.GROUP_MESSAGE.value
MAX_GROUPS = 1000
MAX_TEMPLATES = 100
DEFAULT_TEMPLATE_ID = "all"
TEMPLATE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}", re.ASCII)


def _object(value, limit):
    if not isinstance(value, dict) or len(value) > limit:
        raise SettingsValidationError("名单结构无效或超出数量限制")
    return value


def _rules(value) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > len(TYPE_KEYS)
        or any(not isinstance(key, str) or key not in TYPE_KEYS for key in value)
        or len(set(value)) != len(value)
    ):
        raise SettingsValidationError("规则模板只能选择已支持的平台和解析类型")
    return list(value)


def _template_defaults() -> dict[str, dict]:
    all_rules = []
    video_rules = []
    image_text_rules = []
    for spec in PLATFORM_REGISTRY:
        if not spec.parse_type_controls_ready:
            continue
        for item in spec.parse_types:
            key = f"{spec.key}/{item.key}"
            all_rules.append(key)
            if item.category == "video":
                video_rules.append(key)
            elif item.category == "image_text":
                image_text_rules.append(key)
    return {
        "all": {"name": "全部规则", "rules": all_rules, "builtin": True},
        "video": {"name": "仅视频类规则", "rules": video_rules, "builtin": True},
        "image_text": {
            "name": "仅图文类规则",
            "rules": image_text_rules,
            "builtin": True,
        },
    }


def default_access_payload() -> dict:
    """返回不改变既有解析行为的初始配置。"""
    return {
        "mode": "off",
        "default_template_id": DEFAULT_TEMPLATE_ID,
        "templates": _template_defaults(),
        "groups": {},
        "private": None,
    }


def _permission(
    denied: set[str], *, deny_uncontrolled_platforms: bool = False
) -> "RequestAccess":
    return RequestAccess(
        frozenset(
            key
            for key in PLATFORM_KEYS
            if (
                (deny_uncontrolled_platforms and not PLATFORM_TYPE_KEYS.get(key))
                or (PLATFORM_TYPE_KEYS.get(key) and PLATFORM_TYPE_KEYS[key] <= denied)
            )
        ),
        frozenset(denied),
    )


@dataclass(frozen=True)
class RequestAccess:
    """单条消息的权限快照，不修改共享解析器或全局开关。"""

    denied_platforms: frozenset[str] = frozenset()
    denied_types: frozenset[str] = frozenset()

    def parsers(self, parsers):
        return (
            parser for parser in parsers if parser.name not in self.denied_platforms
        )

    def feature_scope(self):
        """在匹配和解析期间限制类型，退出作用域时恢复原视图。"""
        return restrict_parse_types(self.denied_types)


UNRESTRICTED = RequestAccess()
DENY_ALL = RequestAccess(PLATFORM_KEYS, TYPE_KEYS)


@dataclass(frozen=True)
class AccessRules:
    """解析全局模式、实时模板引用及群聊/私聊规则。"""

    data: dict = field(default_factory=default_access_payload)
    mode: str = "off"
    templates: dict[str, dict] = field(default_factory=_template_defaults)
    groups: dict[str, RequestAccess] = field(default_factory=dict)
    private: RequestAccess | None = None

    @classmethod
    def from_payload(cls, payload: object) -> "AccessRules":
        """校验保存请求，仅允许自建模板覆盖规则和名称。"""
        data = default_access_payload()
        templates = data["templates"]
        if payload is not None:
            supplied = _object(payload, 5)
            if supplied.keys() - {
                "mode",
                "default_template_id",
                "templates",
                "groups",
                "private",
            }:
                raise SettingsValidationError("名单设置无效")
            data.update(supplied)

        mode = data["mode"]
        if mode not in ("off", "blacklist", "whitelist"):
            raise SettingsValidationError("名单模式无效")
        raw_templates = _object(data["templates"], MAX_TEMPLATES)
        builtin_ids = frozenset(templates)
        if len(builtin_ids | raw_templates.keys()) > MAX_TEMPLATES:
            raise SettingsValidationError("模板数量超出限制")
        for template_id, value in raw_templates.items():
            if not isinstance(template_id, str) or not TEMPLATE_ID.fullmatch(
                template_id
            ):
                raise SettingsValidationError("模板 ID 无效")
            value = _object(value, 3)
            if template_id in builtin_ids:
                if value != templates[template_id] or value.get("builtin") is not True:
                    raise SettingsValidationError("内置模板不可修改")
                continue
            name = value.get("name")
            builtin = value.get("builtin", False)
            if (
                value.keys() - {"name", "rules", "builtin"}
                or not isinstance(name, str)
                or not name.strip()
                or not name.isprintable()
                or len(name) > 48
                or type(builtin) is not bool
            ):
                raise SettingsValidationError("模板信息无效")
            templates[template_id] = {
                "name": name.strip(),
                "rules": _rules(value.get("rules", [])),
                "builtin": False,
            }
        default_id = data["default_template_id"]
        if not isinstance(default_id, str) or default_id not in templates:
            raise SettingsValidationError("默认模板必须指向一个现有模板")

        def resolve(entry) -> RequestAccess:
            rules = (
                templates[entry["template_id"]]["rules"]
                if entry["template_id"] is not None
                else entry["rules"]
            )
            selected = set(rules)
            if mode == "off":
                return UNRESTRICTED
            if mode == "blacklist":
                return _permission(selected)
            return _permission(
                set(TYPE_KEYS) - selected,
                deny_uncontrolled_platforms=True,
            )

        groups = {}
        normalized_groups = {}
        for umo, value in _object(data["groups"], MAX_GROUPS).items():
            if (
                not isinstance(umo, str)
                or len(umo) > 384
                or any(c.isspace() or not c.isprintable() for c in umo)
            ):
                raise SettingsValidationError("群聊 UMO 无效")
            instance, _, rest = umo.partition(":")
            kind, _, session = rest.partition(":")
            if not instance or kind != GROUP_MESSAGE or not session:
                raise SettingsValidationError("请填写群聊 UMO")
            normalized = cls._entry(value, templates)
            normalized_groups[umo] = normalized
            groups[umo] = resolve(normalized)

        private = data["private"]
        normalized_private = None if private is None else cls._entry(private, templates)
        return cls(
            data={
                "mode": mode,
                "default_template_id": default_id,
                "templates": templates,
                "groups": normalized_groups,
                "private": normalized_private,
            },
            mode=mode,
            templates=templates,
            groups=groups,
            private=resolve(normalized_private) if normalized_private else None,
        )

    @staticmethod
    def _entry(value, templates):
        value = _object(value, 3)
        label = value.get("label", "")
        template_id = value.get("template_id")
        rules = value.get("rules")
        if (
            value.keys() - {"label", "template_id", "rules"}
            or not isinstance(label, str)
            or len(label) > 80
            or not all(c.isprintable() for c in label)
            or (
                template_id is not None
                and (not isinstance(template_id, str) or template_id not in templates)
            )
            or (template_id is not None and rules is not None)
            or (template_id is None and rules is None)
        ):
            raise SettingsValidationError("名单项无效")
        return {
            "label": label.strip(),
            "template_id": template_id,
            "rules": None if rules is None else _rules(rules),
        }

    @classmethod
    def from_store(cls, payload: object) -> "AccessRules":
        """恢复内置模板定义，并保留存储中的名单引用和自建模板。"""
        if isinstance(payload, dict) and payload and "mode" not in payload:
            raise SettingsValidationError("旧版实验名单无法应用，请重新配置")
        if isinstance(payload, dict) and "enabled" in payload:
            payload = dict(payload)
            enabled = payload.pop("enabled")
            if type(enabled) is not bool or payload["mode"] not in (
                "blacklist",
                "whitelist",
            ):
                raise SettingsValidationError("旧版名单设置无效")
            if not enabled:
                payload["mode"] = "off"
        if isinstance(payload, dict) and "templates" in payload:
            stored_templates = _object(payload["templates"], MAX_TEMPLATES)
            builtin_ids = _template_defaults().keys()
            payload = {
                **payload,
                "templates": {
                    key: value
                    for key, value in stored_templates.items()
                    if key not in builtin_ids
                },
            }
        return cls.from_payload(payload)

    def for_event(self, event) -> RequestAccess:
        """应用名单模式；非群聊/私聊事件不受名单拦截。"""
        if self.mode == "off":
            return UNRESTRICTED
        if event.is_private_chat():
            target = self.private
        else:
            umo = EventIdentity.group_rule_key(event)
            if not umo:
                return UNRESTRICTED
            target = self.groups.get(umo)
        if target is None:
            return DENY_ALL if self.mode == "whitelist" else UNRESTRICTED
        return target
