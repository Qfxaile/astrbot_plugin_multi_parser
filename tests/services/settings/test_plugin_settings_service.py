import asyncio
from copy import deepcopy
from threading import get_ident

import pytest
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
