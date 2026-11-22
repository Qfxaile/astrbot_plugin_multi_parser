from types import SimpleNamespace

import pytest
from astrbot_multi_parser.platforms.registry import PLATFORM_REGISTRY
from astrbot_multi_parser.services.settings import access_rules as access_rules_module
from astrbot_multi_parser.services.settings.access_rules import (
    DEFAULT_TEMPLATE_ID,
    TYPE_KEYS,
    AccessRules,
    RequestAccess,
    default_access_payload,
)
from astrbot_multi_parser.services.settings.native_fields import SettingsValidationError

QQ = "qq:GroupMessage:88"
SLACK = "slack:GroupMessage:user_channel"


def event(umo, *, adapter="aiocqhttp", group="", private=False):
    return SimpleNamespace(
        unified_msg_origin=umo,
        get_platform_id=lambda: umo.partition(":")[0],
        get_platform_name=lambda: adapter,
        get_group_id=lambda: group,
        is_private_chat=lambda: private,
    )


def settings(**changes):
    return {**default_access_payload(), "mode": "blacklist", **changes}


def test_blacklist_denies_only_selected_capabilities_in_listed_qq_group():
    data = settings(
        groups={
            QQ: {
                "label": "开发群",
                "template_id": None,
                "rules": ["douyin/video"],
            }
        }
    )
    rules = AccessRules.from_payload(data)
    for session in ("88", "20_88", "30_88"):
        access = rules.for_event(event(f"qq:GroupMessage:{session}", group="88"))
        assert access.denied_types == {"douyin/video"}
        assert not access.denied_platforms
    assert (
        rules.for_event(event("other:GroupMessage:88", group="88")) == RequestAccess()
    )
    assert rules.for_event(event("qq:GroupMessage:89", group="89")) == RequestAccess()


def test_off_mode_ignores_entries_but_preserves_them_for_reenable():
    rules = AccessRules.from_payload(
        settings(
            mode="off",
            groups={QQ: {"template_id": None, "rules": ["douyin/video"]}},
        )
    )

    assert rules.for_event(event(QQ, group="88")) == RequestAccess()
    assert rules.data["groups"][QQ]["rules"] == ["douyin/video"]


def test_whitelist_denies_nonmembers_and_unselected_types_without_reversal():
    data = settings(
        mode="whitelist",
        groups={
            QQ: {
                "label": "A 群",
                "template_id": None,
                "rules": ["douyin/video"],
            }
        },
    )
    rules = AccessRules.from_payload(data)
    member = rules.for_event(event(QQ, group="88"))
    fully_denied = {
        platform
        for platform in {key.partition("/")[0] for key in TYPE_KEYS}
        if all(
            key in member.denied_types
            for key in TYPE_KEYS
            if key.startswith(f"{platform}/")
        )
    }
    assert member.denied_platforms == fully_denied
    assert "douyin" not in member.denied_platforms
    assert "douyin/video" not in member.denied_types
    assert "douyin/image_text" in member.denied_types
    blocked = rules.for_event(event("qq:GroupMessage:89", group="89"))
    assert blocked.denied_platforms == {spec.key for spec in PLATFORM_REGISTRY}
    assert blocked.denied_types == TYPE_KEYS


def test_whitelist_platform_selection_allows_all_types_but_respects_global_upper_bound():
    rules = AccessRules.from_payload(
        settings(
            mode="whitelist",
            groups={
                QQ: {
                    "template_id": None,
                    "rules": ["douyin/video", "douyin/image_text"],
                }
            },
        )
    )
    access = rules.for_event(event(QQ, group="88"))
    assert "douyin" not in access.denied_platforms
    assert not {"douyin/video", "douyin/image_text"} & access.denied_types


def test_all_private_chats_share_a_template_rule():
    rules = AccessRules.from_payload(
        settings(
            mode="blacklist",
            private={"template_id": None, "rules": ["weibo/video"]},
        )
    )
    for umo in ("qq:FriendMessage:12", "slack:FriendMessage:c:4"):
        assert rules.for_event(event(umo, private=True)).denied_types == {"weibo/video"}


def test_template_updates_are_resolved_live_while_custom_entries_are_detached():
    data = settings(
        templates={
            "custom": {"name": "自定义模板", "rules": ["douyin/video"]},
        },
        groups={
            QQ: {"label": "跟随模板", "template_id": "custom", "rules": None},
            SLACK: {"label": "自定义", "template_id": None, "rules": ["weibo/video"]},
        },
    )
    first = AccessRules.from_payload(data)
    assert first.for_event(event(QQ, group="88")).denied_types == {"douyin/video"}
    data["templates"]["custom"]["rules"] = ["douyin/image_text"]
    updated = AccessRules.from_payload(data)
    assert updated.for_event(event(QQ, group="88")).denied_types == {
        "douyin/image_text"
    }
    assert updated.for_event(
        event(SLACK, adapter="slack", group="channel")
    ).denied_types == {"weibo/video"}


def test_default_template_and_prebuilt_category_templates():
    rules = AccessRules.from_payload({})
    assert rules.data["default_template_id"] == DEFAULT_TEMPLATE_ID
    assert set(rules.templates["all"]["rules"]) == set(TYPE_KEYS)
    assert rules.templates["video"]["rules"]
    assert rules.templates["image_text"]["rules"]
    assert not set(rules.templates["video"]["rules"]) & set(
        rules.templates["image_text"]["rules"]
    )


def test_uncontrolled_platform_is_only_blocked_by_whitelist(monkeypatch):
    monkeypatch.setattr(
        access_rules_module,
        "PLATFORM_KEYS",
        access_rules_module.PLATFORM_KEYS | {"future"},
    )

    blacklist = access_rules_module._permission(set())
    whitelist = access_rules_module._permission(
        set(TYPE_KEYS), deny_uncontrolled_platforms=True
    )

    assert "future" not in blacklist.denied_platforms
    assert "future" in whitelist.denied_platforms


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "invalid"},
        {"default_template_id": "missing"},
        {"templates": {"x": {"name": "", "rules": []}}},
        {"templates": {"x": {"name": "x", "rules": ["missing"]}}},
        {"groups": {"qq:FriendMessage:12": {"template_id": "all", "rules": None}}},
        {"groups": {QQ: {"template_id": "missing", "rules": None}}},
        {"groups": {QQ: {"template_id": None, "rules": ["missing"]}}},
        {"private": {"template_id": "all", "rules": []}},
        {"unknown": True},
    ],
)
def test_reject_invalid_templates_or_targets(payload):
    with pytest.raises(SettingsValidationError):
        AccessRules.from_payload({**default_access_payload(), **payload})


def test_legacy_unpublished_experiment_payload_does_not_activate_old_rules():
    with pytest.raises(SettingsValidationError):
        AccessRules.from_store(
            {
                "enabled": True,
                "group_lists": {"douyin/video": {"mode": "whitelist", "members": [QQ]}},
            }
        )
