import asyncio
import json
from copy import deepcopy
from pathlib import Path
from threading import get_ident

import pytest
from astrbot_multi_parser.core.http import http_client_proxy_options
from astrbot_multi_parser.core.settings import PluginSettings
from astrbot_multi_parser.platforms.github.parser import GitHubParser
from astrbot_multi_parser.services.settings.runtime_config import RuntimeSettingsConfig
from astrbot_multi_parser.services.settings.service import (
    FEATURES_KEY,
    PluginSettingsService,
    SettingsValidationError,
)


class Store:
    def __init__(self, value=None):
        self.values = {FEATURES_KEY: value} if value is not None else {}

    async def get(self, key, default):
        return self.values.get(key, default)

    async def put(self, key, value):
        self.values[key] = deepcopy(value)


class Config(dict):
    def __init__(self, *args, fail_first_save=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_calls = 0
        self.fail_first_save = fail_first_save

    def save_config(self):
        self.save_calls += 1
        if self.fail_first_save and self.save_calls == 1:
            raise OSError("disk failure")


def make_service(config=None, store=None):
    store = store or Store()
    config = config if config is not None else {}
    runtime = RuntimeSettingsConfig(config)
    service = PluginSettingsService(
        config,
        runtime,
        read_store=store.get,
        write_store=store.put,
    )
    return service, runtime, store, config


def test_web_menus_cover_native_schema_without_exposing_internal_fields():
    schema_path = Path(__file__).resolve().parents[3] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    service, _, _, _ = make_service({"internal_option": "hidden"})

    groups = service.snapshot()["groups"]
    assert set(groups) == {"delivery", "ai", "network"}
    fields = [
        field for cards in groups.values() for card in cards for field in card["fields"]
    ]
    group_values = {field["key"]: field["value"] for field in fields}
    elsewhere = {"platform_switches", "proxy_switches", "cookies", "github_token"}
    assert len(group_values) == len(fields)
    assert group_values == {
        key: value["default"] for key, value in schema.items() if key not in elsewhere
    }
    assert {field["key"] for field in fields if field["type"] == "provider"} == {
        "ai_summary_text_provider_id",
        "ai_summary_vision_provider_id",
    }


async def test_native_menu_updates_live_values_and_keeps_unrelated_config():
    config = Config(forward_mode="always", github_token="keep", internal_option=3)
    service, runtime, store, _ = make_service(config)
    settings = PluginSettings(runtime)
    assert settings.text("forward_mode") == "always"
    prompt = "第一行\n第二行\n"

    snapshot = await service.update(
        {
            "revision": 0,
            "settings": {
                "forward_mode": "never",
                "parse_mode": "tool",
                "enable_ai_summary": True,
                "ai_summary_text_provider_id": "summarizer",
                "ai_summary_prompt": prompt,
                "request_timeout_seconds": 45,
                "size_check_timeout_seconds": 2.5,
            },
        }
    )

    assert settings.text("forward_mode") == "never"
    assert settings.text("parse_mode") == "tool"
    assert settings.boolean("enable_ai_summary") is True
    assert settings.text("ai_summary_text_provider_id") == "summarizer"
    assert settings.integer("request_timeout_seconds") == 45
    assert settings.decimal("size_check_timeout_seconds") == 2.5
    group_values = {
        field["key"]: field["value"]
        for cards in snapshot["groups"].values()
        for card in cards
        for field in card["fields"]
    }
    assert group_values["ai_summary_prompt"] == prompt
    assert group_values["forward_mode"] == "never"
    assert config["github_token"] == "keep"
    assert config["internal_option"] == 3
    assert config.save_calls == 1
    assert store.values[FEATURES_KEY]["revision"] == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"forward_mode": "unknown"},
        {"non_forward_message_limit": -2},
        {"enable_ai_summary": "true"},
        {"request_timeout_seconds": True},
        {"size_check_timeout_seconds": float("nan")},
        {"ai_summary_timeout_seconds": float("inf")},
        {"image_download_concurrency": 17},
        {"image_download_concurrency": 0},
        {"ai_summary_max_input_chars": 999},
        {"github_token": "wrong section"},
        {"platform_feature_switches": {}},
    ],
)
async def test_invalid_native_menu_values_leave_config_and_revision_unchanged(invalid):
    service, _, store, config = make_service()

    with pytest.raises(SettingsValidationError):
        await service.update(
            {"revision": 0, "settings": {"parse_mode": "tool", **invalid}}
        )

    assert config == store.values == {}
    assert service.snapshot()["revision"] == 0


async def test_proxy_requires_valid_url_and_updates_individual_platforms():
    service, runtime, _, config = make_service(Config(proxy_switches={"weibo": False}))

    with pytest.raises(SettingsValidationError, match="代理"):
        await service.update({"revision": 0, "proxy_switches": {"bilibili": True}})
    with pytest.raises(SettingsValidationError, match="代理"):
        await service.update(
            {"revision": 0, "settings": {"proxy_url": "socks5://proxy.example:1080"}}
        )
    with pytest.raises(SettingsValidationError, match="开关"):
        await service.update({"revision": 0, "proxy_switches": {"unknown": True}})

    url = "http://user:password@proxy.example:8080"
    applied = await service.update(
        {
            "revision": 0,
            "settings": {"proxy_url": url},
            "proxy_switches": {"bilibili": True},
        }
    )
    assert config["proxy_switches"] == {"weibo": False, "bilibili": True}
    platform = next(item for item in applied["platforms"] if item["key"] == "bilibili")
    assert platform["proxy_enabled"] is True
    assert http_client_proxy_options(runtime, "bilibili") == {
        "trust_env": False,
        "proxy": url,
    }
    assert http_client_proxy_options(runtime, "weibo") == {"trust_env": False}

    with pytest.raises(SettingsValidationError, match="清空"):
        await service.update({"revision": 1, "settings": {"proxy_url": ""}})
    await service.update(
        {
            "revision": 1,
            "settings": {"proxy_url": ""},
            "proxy_switches": {"bilibili": False},
        }
    )

    assert config["proxy_url"] == ""
    assert http_client_proxy_options(runtime, "bilibili") == {"trust_env": False}


async def test_native_menu_save_failure_rolls_back_existing_and_new_values():
    config = Config(forward_mode="always", fail_first_save=True)
    service, runtime, store, _ = make_service(config)

    with pytest.raises(OSError, match="disk failure"):
        await service.update(
            {
                "revision": 0,
                "settings": {
                    "forward_mode": "never",
                    "proxy_url": "https://proxy.example",
                },
                "proxy_switches": {"github": True},
            }
        )

    assert config == {"forward_mode": "always"}
    assert runtime["forward_mode"] == "always"
    assert "proxy_switches" not in runtime
    assert store.values == {}
    assert service.snapshot()["revision"] == 0


async def test_web_snapshot_waits_for_pending_write_and_reads_rolled_back_values():
    started = asyncio.Event()
    release = asyncio.Event()

    class FailingStore(Store):
        async def put(self, key, value):
            started.set()
            await release.wait()
            raise OSError("storage failure")

    service, _, _, _ = make_service(Config(forward_mode="always"), FailingStore())
    saving = asyncio.create_task(
        service.update({"revision": 0, "settings": {"forward_mode": "never"}})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        reading = asyncio.create_task(service.read_snapshot())
        await asyncio.sleep(0)
        assert not reading.done()
    finally:
        release.set()
        with pytest.raises(OSError, match="storage failure"):
            await asyncio.wait_for(saving, timeout=2)

    snapshot = await asyncio.wait_for(reading, timeout=2)
    assert snapshot["revision"] == 0
    delivery_fields = [
        field for card in snapshot["groups"]["delivery"] for field in card["fields"]
    ]
    assert (
        next(
            field["value"]
            for field in delivery_fields
            if field["key"] == "forward_mode"
        )
        == "always"
    )


async def test_native_update_awaits_async_save_without_waiting_for_other_tasks():
    started = asyncio.Event()
    release = asyncio.Event()

    class AsyncConfig(Config):
        async def save_config_async(self):
            started.set()
            await release.wait()

    config = AsyncConfig()
    service, runtime, _, _ = make_service(config)
    saving = asyncio.create_task(
        service.update({"revision": 0, "github_token": "test-token"})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert runtime.get("github_token") == "test-token"
        assert not saving.done()
    finally:
        release.set()
        snapshot = await asyncio.wait_for(saving, timeout=2)

    assert snapshot["revision"] == 1
    assert config.save_calls == 0


async def test_legacy_native_save_runs_outside_the_event_loop():
    loop_thread = get_ident()

    class SyncConfig(Config):
        def save_config(self):
            assert get_ident() != loop_thread
            super().save_config()

    config = SyncConfig()
    service, _, _, _ = make_service(config)

    snapshot = await service.update({"revision": 0, "github_token": "test-token"})

    assert snapshot["revision"] == 1
    assert config.save_calls == 1


async def test_cancelled_request_finishes_persisting_settings():
    started = asyncio.Event()
    release = asyncio.Event()

    class AsyncConfig(Config):
        async def save_config_async(self):
            started.set()
            await release.wait()

    service, runtime, store, _ = make_service(AsyncConfig())
    saving = asyncio.create_task(
        service.update({"revision": 0, "github_token": "test-token"})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        saving.cancel()
        await asyncio.sleep(0)
        assert not saving.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(saving, timeout=2)
    assert runtime.get("github_token") == "test-token"
    assert store.values[FEATURES_KEY]["revision"] == service.snapshot()["revision"] == 1


@pytest.mark.asyncio
async def test_load_and_update_keep_parse_features_out_of_native_config():
    service, runtime, store, config = make_service()
    await service.load()
    initial = service.snapshot()
    assert initial["platforms"][0]["parse_types"][0]["enabled"] is True

    updated = await service.update(
        {
            "revision": 0,
            "parse_types": {"bilibili": {"video": False}},
        }
    )

    assert updated["revision"] == 1
    assert runtime["platform_feature_switches"]["bilibili"]["video"] is False
    assert "platform_feature_switches" not in config
    assert store.values[FEATURES_KEY]["platforms"]["bilibili"]["video"] is False


@pytest.mark.asyncio
async def test_snapshot_returns_plaintext_cookies_and_partial_update_preserves_other_settings():
    config = {
        "platform_switches": {"bilibili": True, "pixiv": False},
        "cookies": {
            "bilibili_cookies": "SESSDATA=secret",
            "douyin_cookies": "session=keep",
        },
    }
    service, _, _, config = make_service(config)
    await service.load()
    snapshot = service.snapshot()
    bilibili = next(item for item in snapshot["platforms"] if item["key"] == "bilibili")
    assert bilibili["cookie"] == {
        "supported": True,
        "value": "SESSDATA=secret",
    }
    assert (
        next(item for item in snapshot["platforms"] if item["key"] == "github")[
            "parse_type_controls_ready"
        ]
        is True
    )
    assert (
        next(item for item in snapshot["platforms"] if item["key"] == "douyin")[
            "parse_type_controls_ready"
        ]
        is True
    )

    await service.update(
        {
            "revision": 0,
            "platform_switches": {"bilibili": False},
            "cookies": {"bilibili": "SESSDATA=replaced"},
        }
    )

    assert config["platform_switches"] == {"bilibili": False, "pixiv": False}
    assert config["cookies"] == {
        "bilibili_cookies": "SESSDATA=replaced",
        "douyin_cookies": "session=keep",
    }


@pytest.mark.asyncio
async def test_invalid_payload_and_stale_revision_do_not_mutate_settings():
    service, runtime, store, config = make_service()
    await service.load()
    with pytest.raises(SettingsValidationError):
        await service.update(
            {"revision": 0, "parse_types": {"unknown": {"video": False}}}
        )
    assert runtime["platform_feature_switches"]["bilibili"]["video"] is True
    assert store.values == {}
    assert config == {}

    await service.update({"revision": 0, "parse_types": {"bilibili": {"video": False}}})
    with pytest.raises(SettingsValidationError, match="重新加载"):
        await service.update(
            {"revision": 0, "parse_types": {"bilibili": {"video": True}}}
        )
    assert runtime["platform_feature_switches"]["bilibili"]["video"] is False


@pytest.mark.asyncio
async def test_cookie_clear_is_explicit_and_preserves_other_platform_cookies():
    config = {
        "cookies": {
            "bilibili_cookies": "SESSDATA=remove",
            "douyin_cookies": "session=keep",
        }
    }
    service, _, _, config = make_service(config)
    await service.load()

    snapshot = await service.update({"revision": 0, "clear_cookies": ["bilibili"]})

    assert config["cookies"] == {
        "bilibili_cookies": "",
        "douyin_cookies": "session=keep",
    }
    bilibili = next(item for item in snapshot["platforms"] if item["key"] == "bilibili")
    assert bilibili["cookie"]["value"] == ""


@pytest.mark.asyncio
async def test_invalid_cookie_clear_does_not_partially_update_settings():
    service, runtime, store, config = make_service()
    await service.load()

    with pytest.raises(SettingsValidationError):
        await service.update(
            {
                "revision": 0,
                "platform_switches": {"bilibili": False},
                "clear_cookies": ["unknown"],
            }
        )

    assert config == {}
    assert store.values == {}
    assert runtime["platform_feature_switches"]["bilibili"]["video"] is True


@pytest.mark.asyncio
async def test_github_token_is_returned_as_plaintext_and_updates_the_existing_parser():
    config = Config({"github_token": "ghp_old_test_value"})
    service, runtime, _, _ = make_service(config)
    parser = GitHubParser(runtime)

    updated = await service.update(
        {"revision": 0, "github_token": "ghp_new_test_value"}
    )

    def github(snapshot):
        return next(item for item in snapshot["platforms"] if item["key"] == "github")

    assert github(updated)["token"] == {"value": "ghp_new_test_value"}
    assert github(service.snapshot())["token"] == {"value": "ghp_new_test_value"}
    assert (
        parser._repository_request_headers()["Authorization"]
        == "Bearer ghp_new_test_value"
    )
    assert config.save_calls == 1

    cleared = await service.update({"revision": 1, "github_token": ""})

    assert github(cleared)["token"] == {"value": ""}
    assert "Authorization" not in parser._repository_request_headers()
    assert config.save_calls == 2


@pytest.mark.parametrize("token", [None, False, "ghp_bad\nvalue", "中文", "x" * 513])
async def test_rejects_invalid_github_token_without_mutating_config(token):
    service, _, store, config = make_service()
    with pytest.raises(SettingsValidationError, match="Token"):
        await service.update({"revision": 0, "github_token": token})

    assert config == {}
    assert store.values == {}


@pytest.mark.asyncio
async def test_native_config_is_saved_and_restored_if_plugin_store_fails():
    class FailingStore(Store):
        async def put(self, key, value):
            raise OSError("plugin storage failure")

    config = Config({"platform_switches": {"bilibili": True}, "github_token": "old"})
    service, runtime, _, _ = make_service(config, FailingStore())
    await service.load()

    with pytest.raises(OSError, match="plugin storage failure"):
        await service.update(
            {
                "revision": 0,
                "platform_switches": {"bilibili": False},
                "github_token": "new",
                "parse_types": {"bilibili": {"video": False}},
            }
        )

    assert config["platform_switches"]["bilibili"] is True
    assert config["github_token"] == "old"
