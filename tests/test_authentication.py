import asyncio
from types import SimpleNamespace

import pytest
from astrbot.api.message_components import Image, Plain
from astrbot_multi_parser.core.platform_login import (
    LoginPollResult,
    LoginPollState,
    PlatformLoginError,
    PlatformLoginProvider,
    PlatformUser,
    QRLoginChallenge,
)
from astrbot_multi_parser.services.authentication import AuthenticationService


class SavingConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_calls = 0

    def save_config(self):
        self.save_calls += 1


class FailingSavingConfig(SavingConfig):
    def save_config(self):
        super().save_config()
        raise RuntimeError("disk failure")


class FakeEvent:
    def __init__(self, session_id="adapter:private:admin"):
        self.unified_msg_origin = session_id
        self.sent = []

    async def send(self, message):
        self.sent.append(list(message.chain))


class FakeLoginProvider(PlatformLoginProvider):
    display_name = "B站"
    cookie_config_key = "bilibili_cookies"

    def __init__(self, results, user=None, user_error=None):
        self.results = list(results)
        self.user = user
        self.user_error = user_error
        self.user_cookie_headers = []
        self.closed = False

    async def create_qr_challenge(self):
        return QRLoginChallenge("secret-key", b"png-data", 30)

    async def poll_qr_status(self, session_key):
        assert session_key == "secret-key"
        return self.results.pop(0)

    async def get_current_user(self, cookie_header):
        self.user_cookie_headers.append(cookie_header)
        if self.user_error is not None:
            raise self.user_error
        return self.user

    async def close(self):
        self.closed = True


class FakeWeiboLoginProvider(FakeLoginProvider):
    display_name = "微博"
    cookie_config_key = "weibo_cookies"


@pytest.mark.asyncio
async def test_login_formats_platform_errors_consistently():
    class FailedProvider(FakeLoginProvider):
        display_name = "抖音"
        cookie_config_key = "douyin_cookies"

        async def create_qr_challenge(self):
            raise PlatformLoginError("抖音登录触发了平台人机或设备验证。")

    provider = FailedProvider([])
    service = AuthenticationService(
        {},
        provider_factories={"抖音": lambda: provider},
    )

    message = await service.login(FakeEvent(), "抖音")

    assert message == ("登录失败｜平台：抖音｜原因：触发了平台人机或设备验证。")
    assert provider.closed is True


@pytest.mark.asyncio
async def test_login_formats_expired_qr_consistently():
    provider = FakeLoginProvider([LoginPollResult(LoginPollState.EXPIRED)])
    service = AuthenticationService(
        {},
        provider_factories={"B站": lambda: provider},
    )

    message = await service.login(FakeEvent(), "B站")

    assert message == (
        "登录失败｜平台：B站｜原因：二维码已过期，请重新发起登录。"
        "该平台短信登录需要额外人机验证，当前私聊流程暂不支持。"
    )
    assert provider.closed is True


@pytest.mark.asyncio
async def test_login_sends_qr_and_saves_cookie_without_echoing_secret():
    config = SavingConfig(cookies={"bilibili_cookies": ""})
    provider = FakeLoginProvider(
        [
            LoginPollResult(LoginPollState.SCANNED),
            LoginPollResult(
                LoginPollState.SUCCESS,
                "SESSDATA=session-secret; bili_jct=csrf-secret",
            ),
        ],
        user=PlatformUser(user_id="12345", display_name="测试用户"),
    )
    service = AuthenticationService(
        config,
        provider_factories={"B站": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 0
    event = FakeEvent()

    message = await service.login(event, "B站")

    assert message == "B站登录成功，Cookies 已保存。当前用户：测试用户（UID：12345）。"
    assert config["cookies"]["bilibili_cookies"].startswith("SESSDATA=")
    assert config.save_calls == 1
    assert provider.closed is True
    assert provider.user_cookie_headers == [
        "SESSDATA=session-secret; bili_jct=csrf-secret"
    ]
    assert isinstance(event.sent[0][0], Plain)
    assert isinstance(event.sent[0][1], Image)
    assert event.sent[0][1].file == "base64://cG5nLWRhdGE="
    visible_text = "".join(
        component.text
        for chain in event.sent
        for component in chain
        if isinstance(component, Plain)
    )
    assert "secret-key" not in visible_text
    assert "session-secret" not in visible_text
    assert "session-secret" not in message


@pytest.mark.asyncio
async def test_logout_during_user_lookup_prevents_login_success_and_cookie_restore():
    lookup_started = asyncio.Event()
    finish_lookup = asyncio.Event()

    class SlowUserProvider(FakeLoginProvider):
        async def get_current_user(self, cookie_header):
            self.user_cookie_headers.append(cookie_header)
            lookup_started.set()
            await finish_lookup.wait()
            return self.user

    config = SavingConfig(cookies={"bilibili_cookies": "SESSDATA=previous-secret"})
    provider = SlowUserProvider(
        [LoginPollResult(LoginPollState.SUCCESS, "SESSDATA=new-secret")],
        user=PlatformUser(user_id="12345", display_name="测试用户"),
    )
    service = AuthenticationService(
        config,
        provider_factories={"B站": lambda: provider},
    )
    login_task = asyncio.create_task(
        service.login(FakeEvent("adapter:private:owner"), "B站")
    )
    await lookup_started.wait()

    assert await service.logout("B站") == "B站已退出登录，Cookies 已清除。"
    finish_lookup.set()

    assert await login_task is None
    assert config["cookies"]["bilibili_cookies"] == ""
    assert config.save_calls == 1
    assert provider.closed is True


@pytest.mark.asyncio
async def test_cancel_only_stops_login_from_same_private_session():
    started = asyncio.Event()

    class WaitingProvider(FakeLoginProvider):
        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    provider = WaitingProvider([])
    provider.display_name = "小红书"
    provider.cookie_config_key = "redbook_cookies"
    service = AuthenticationService(
        {},
        provider_factories={"小红书": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:owner")
    other = FakeEvent("adapter:private:other")
    login_task = asyncio.create_task(service.login(owner, "小红书"))
    await started.wait()

    assert await service.cancel(other) == "当前私聊没有进行中的平台登录。"
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None
    assert provider.closed is True


@pytest.mark.asyncio
async def test_logout_clears_cookie_and_saves_config():
    config = SavingConfig(
        cookies={"bilibili_cookies": "SESSDATA=session-secret"}
    )
    service = AuthenticationService(config, provider_factories={"B站": lambda: None})

    message = await service.logout("B站")

    assert message == "B站已退出登录，Cookies 已清除。"
    assert config["cookies"]["bilibili_cookies"] == ""
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_douyin_same_platform_login_is_exclusive():
    started = asyncio.Event()

    class WaitingDouyinProvider(FakeLoginProvider):
        display_name = "抖音"
        cookie_config_key = "douyin_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingDouyinProvider([])
    second_provider = WaitingDouyinProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"抖音": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:owner")
    login_task = asyncio.create_task(service.login(owner, "抖音"))
    await started.wait()

    duplicate_message = await service.login(FakeEvent(), "抖音")

    assert duplicate_message == "抖音已有登录流程正在进行，请先取消或等待结束。"
    assert second_provider.closed is True
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None


@pytest.mark.asyncio
async def test_redbook_same_platform_login_is_exclusive():
    started = asyncio.Event()

    class WaitingRedBookProvider(FakeLoginProvider):
        display_name = "小红书"
        cookie_config_key = "redbook_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingRedBookProvider([])
    second_provider = WaitingRedBookProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"小红书": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:owner")
    login_task = asyncio.create_task(service.login(owner, "小红书"))
    await started.wait()

    duplicate_message = await service.login(FakeEvent(), "小红书")

    assert duplicate_message == "小红书已有登录流程正在进行，请先取消或等待结束。"
    assert second_provider.closed is True
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None


@pytest.mark.asyncio
async def test_close_cancels_redbook_login_during_plugin_unload():
    started = asyncio.Event()

    class WaitingRedBookProvider(FakeLoginProvider):
        display_name = "小红书"
        cookie_config_key = "redbook_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    provider = WaitingRedBookProvider([])
    service = AuthenticationService(
        {},
        provider_factories={"小红书": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 60
    login_task = asyncio.create_task(
        service.login(FakeEvent("adapter:private:owner"), "小红书")
    )
    await started.wait()

    await service.close()

    assert await login_task is None
    assert provider.closed is True


@pytest.mark.asyncio
async def test_douyin_logout_restores_cookie_when_save_fails():
    config = FailingSavingConfig(douyin_cookies="sessionid=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"抖音": lambda: None},
    )

    message = await service.logout("抖音")

    assert message == "Cookies 保存失败，原配置未被修改。"
    assert config["douyin_cookies"] == "sessionid=session-secret"
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_wechat_same_platform_login_is_exclusive_and_cancel_is_private():
    started = asyncio.Event()

    class WaitingWeChatProvider(FakeLoginProvider):
        display_name = "微信"
        cookie_config_key = "wechat_yuanbao_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingWeChatProvider([])
    second_provider = WaitingWeChatProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"微信": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:wechat-owner")
    other = FakeEvent("adapter:private:other")
    login_task = asyncio.create_task(service.login(owner, "微信"))
    await started.wait()

    duplicate_message = await service.login(other, "微信")

    assert duplicate_message == "微信已有登录流程正在进行，请先取消或等待结束。"
    assert second_provider.closed is True
    assert await service.cancel(other) == "当前私聊没有进行中的平台登录。"
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None
    assert first_provider.closed is True


@pytest.mark.asyncio
async def test_wechat_logout_restores_cookie_when_save_fails():
    original_cookie = "hy_user=user-secret; hy_token=token-secret"
    config = FailingSavingConfig(wechat_yuanbao_cookies=original_cookie)
    service = AuthenticationService(
        config,
        provider_factories={"微信": lambda: None},
    )

    message = await service.logout("微信")

    assert message == "Cookies 保存失败，原配置未被修改。"
    assert config["wechat_yuanbao_cookies"] == original_cookie


@pytest.mark.asyncio
async def test_redbook_logout_restores_cookie_when_save_fails():
    config = FailingSavingConfig(redbook_cookies="web_session=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"小红书": lambda: None},
    )

    message = await service.logout("小红书")

    assert message == "Cookies 保存失败，原配置未被修改。"
    assert config["redbook_cookies"] == "web_session=session-secret"
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_wechat_login_restores_cookie_when_save_fails_without_leaking_it():
    original_cookie = "hy_user=old-user; hy_token=old-token"

    class SuccessfulWeChatProvider(FakeLoginProvider):
        display_name = "微信"
        cookie_config_key = "wechat_yuanbao_cookies"

    config = FailingSavingConfig(wechat_yuanbao_cookies=original_cookie)
    provider = SuccessfulWeChatProvider(
        [
            LoginPollResult(
                LoginPollState.SUCCESS,
                "hy_user=new-user; hy_token=new-token",
            )
        ]
    )
    service = AuthenticationService(
        config,
        provider_factories={"微信": lambda: provider},
    )

    message = await service.login(FakeEvent(), "微信")

    assert message == ("登录失败｜平台：微信｜原因：Cookies 保存失败，原配置未被修改。")
    assert config["wechat_yuanbao_cookies"] == original_cookie
    assert config.save_calls == 1
    assert provider.closed is True
    assert "old-user" not in message
    assert "new-user" not in message


@pytest.mark.asyncio
async def test_tieba_same_platform_login_is_exclusive_and_cancel_is_isolated():
    started = asyncio.Event()

    class WaitingTiebaProvider(FakeLoginProvider):
        display_name = "贴吧"
        cookie_config_key = "tieba_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingTiebaProvider([])
    second_provider = WaitingTiebaProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"贴吧": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:tieba-owner")
    other = FakeEvent("adapter:private:other")
    login_task = asyncio.create_task(service.login(owner, "贴吧"))
    await started.wait()

    duplicate_message = await service.login(other, "贴吧")

    assert duplicate_message == "贴吧已有登录流程正在进行，请先取消或等待结束。"
    assert second_provider.closed is True
    assert await service.cancel(other) == "当前私聊没有进行中的平台登录。"
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None
    assert first_provider.closed is True


@pytest.mark.asyncio
async def test_tieba_login_restores_cookie_when_save_fails():
    config = FailingSavingConfig(tieba_cookies="BDUSS=previous-secret")

    class SuccessfulTiebaProvider(FakeLoginProvider):
        display_name = "贴吧"
        cookie_config_key = "tieba_cookies"

    provider = SuccessfulTiebaProvider(
        [LoginPollResult(LoginPollState.SUCCESS, "BDUSS=new-secret")]
    )
    service = AuthenticationService(
        config,
        provider_factories={"贴吧": lambda: provider},
    )

    message = await service.login(FakeEvent(), "贴吧")

    assert message == ("登录失败｜平台：贴吧｜原因：Cookies 保存失败，原配置未被修改。")
    assert config["tieba_cookies"] == "BDUSS=previous-secret"
    assert config.save_calls == 1
    assert provider.closed is True


@pytest.mark.asyncio
async def test_tieba_logout_clears_cookie_and_save_failure_rolls_back():
    config = FailingSavingConfig(tieba_cookies="BDUSS=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"贴吧": lambda: None},
    )

    message = await service.logout("贴吧")

    assert message == "Cookies 保存失败，原配置未被修改。"
    assert config["tieba_cookies"] == "BDUSS=session-secret"
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_tieba_logout_clears_cookie_and_saves_config():
    config = SavingConfig(tieba_cookies="BDUSS=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"贴吧": lambda: None},
    )

    message = await service.logout("贴吧")

    assert message == "贴吧已退出登录，Cookies 已清除。"
    assert config["tieba_cookies"] == ""
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_tieba_close_releases_active_login_on_plugin_unload():
    started = asyncio.Event()

    class WaitingTiebaProvider(FakeLoginProvider):
        display_name = "贴吧"
        cookie_config_key = "tieba_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    provider = WaitingTiebaProvider([])
    service = AuthenticationService(
        {},
        provider_factories={"贴吧": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 60
    login_task = asyncio.create_task(service.login(FakeEvent(), "贴吧"))
    await started.wait()

    await service.close()

    assert await login_task is None
    assert provider.closed is True


@pytest.mark.asyncio
async def test_xiaoheihe_login_uses_native_scanner_label_and_saves_cookie():
    class XiaoheiheProvider(FakeLoginProvider):
        display_name = "小黑盒"
        cookie_config_key = "xiaoheihe_cookies"

    config = SavingConfig()
    provider = XiaoheiheProvider(
        [
            LoginPollResult(
                LoginPollState.SUCCESS,
                "pkey=session-secret; x_xhh_tokenid=Bdevice-secret",
            )
        ]
    )
    service = AuthenticationService(
        config,
        provider_factories={"小黑盒": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 0
    event = FakeEvent()

    message = await service.login(event, "小黑盒")

    assert message == "小黑盒登录成功，Cookies 已保存。当前用户信息获取失败。"
    assert config["xiaoheihe_cookies"].startswith("pkey=")
    assert event.sent[0][0].text.startswith("请使用小黑盒客户端扫描二维码")
    visible_text = "".join(
        component.text
        for chain in event.sent
        for component in chain
        if isinstance(component, Plain)
    )
    assert "session-secret" not in visible_text
    assert "Bdevice-secret" not in visible_text


@pytest.mark.asyncio
async def test_xiaoheihe_same_platform_login_and_cancel_are_private_isolated():
    started = asyncio.Event()

    class WaitingXiaoheiheProvider(FakeLoginProvider):
        display_name = "小黑盒"
        cookie_config_key = "xiaoheihe_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingXiaoheiheProvider([])
    second_provider = WaitingXiaoheiheProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"小黑盒": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:owner")
    other = FakeEvent("adapter:private:other")
    login_task = asyncio.create_task(service.login(owner, "小黑盒"))
    await started.wait()

    duplicate_message = await service.login(other, "小黑盒")

    assert duplicate_message == "小黑盒已有登录流程正在进行，请先取消或等待结束。"
    assert second_provider.closed is True
    assert await service.cancel(other) == "当前私聊没有进行中的平台登录。"
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None
    assert first_provider.closed is True


@pytest.mark.asyncio
async def test_xiaoheihe_logout_restores_cookie_when_save_fails():
    config = FailingSavingConfig(
        xiaoheihe_cookies="pkey=session-secret; x_xhh_tokenid=Bdevice-secret"
    )
    service = AuthenticationService(
        config,
        provider_factories={"小黑盒": lambda: None},
    )

    message = await service.logout("小黑盒")

    assert message == "Cookies 保存失败，原配置未被修改。"
    assert config["xiaoheihe_cookies"].startswith("pkey=session-secret")
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_xiaoheihe_login_restores_cookie_when_save_fails():
    class XiaoheiheProvider(FakeLoginProvider):
        display_name = "小黑盒"
        cookie_config_key = "xiaoheihe_cookies"

    original_cookie = "pkey=old-secret; x_xhh_tokenid=Bold-device"
    config = FailingSavingConfig(xiaoheihe_cookies=original_cookie)
    provider = XiaoheiheProvider(
        [
            LoginPollResult(
                LoginPollState.SUCCESS,
                "pkey=new-secret; x_xhh_tokenid=Bnew-device",
            )
        ]
    )
    service = AuthenticationService(
        config,
        provider_factories={"小黑盒": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 0

    message = await service.login(FakeEvent(), "小黑盒")

    assert message == (
        "登录失败｜平台：小黑盒｜原因：Cookies 保存失败，原配置未被修改。"
    )
    assert config["xiaoheihe_cookies"] == original_cookie
    assert provider.closed is True


@pytest.mark.asyncio
async def test_xiaoheihe_logout_clears_cookie_and_saves_config():
    config = SavingConfig(
        xiaoheihe_cookies="pkey=session-secret; x_xhh_tokenid=Bdevice-secret"
    )
    service = AuthenticationService(
        config,
        provider_factories={"小黑盒": lambda: None},
    )

    message = await service.logout("小黑盒")

    assert message == "小黑盒已退出登录，Cookies 已清除。"
    assert config["xiaoheihe_cookies"] == ""
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_close_cleans_xiaoheihe_login_session():
    started = asyncio.Event()

    class WaitingXiaoheiheProvider(FakeLoginProvider):
        display_name = "小黑盒"
        cookie_config_key = "xiaoheihe_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    provider = WaitingXiaoheiheProvider([])
    service = AuthenticationService(
        {},
        provider_factories={"小黑盒": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 60
    login_task = asyncio.create_task(service.login(FakeEvent(), "小黑盒"))
    await started.wait()

    await service.close()

    assert await login_task is None
    assert provider.closed is True
    assert await service.status() == "平台登录状态：\n- 小黑盒：未配置"


@pytest.mark.asyncio
async def test_default_authentication_service_supports_all_login_providers():
    service = AuthenticationService(
        {
            "cookies": {
                "bilibili_cookies": "",
                "douyin_cookies": "",
                "redbook_cookies": "",
                "tieba_cookies": "",
                "weibo_cookies": "",
                "wechat_yuanbao_cookies": "",
                "xiaoheihe_cookies": "",
                "zhihu_cookies": "",
            }
        }
    )

    assert service.supported_platforms == (
        "B站",
        "抖音",
        "小红书",
        "贴吧",
        "微博",
        "微信",
        "小黑盒",
        "知乎",
    )
    assert "Pixiv" not in service.supported_platforms
    assert await service.status() == (
        "平台登录状态：\n- B站：未配置\n- 抖音：未配置\n"
        "- 小红书：未配置\n- 贴吧：未配置\n- 微博：未配置\n"
        "- 微信：未配置\n"
        "- 小黑盒：未配置\n"
        "- 知乎：未配置"
    )
    assert "暂不支持“redbook”" in service._unsupported_platform_message("redbook")
    assert "暂不支持“tieba”" in service._unsupported_platform_message("tieba")
    assert "暂不支持“weibo”" in service._unsupported_platform_message("weibo")
    assert "暂不支持“wechat”" in service._unsupported_platform_message("wechat")
    assert "暂不支持“小黑盒登录”" in service._unsupported_platform_message("小黑盒登录")


@pytest.mark.asyncio
async def test_zhihu_same_platform_login_is_exclusive_and_cancel_is_isolated():
    started = asyncio.Event()

    class WaitingZhihuProvider(FakeLoginProvider):
        display_name = "知乎"
        cookie_config_key = "zhihu_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingZhihuProvider([])
    second_provider = WaitingZhihuProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"知乎": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:zhihu-owner")
    other = FakeEvent("adapter:private:other")
    login_task = asyncio.create_task(service.login(owner, "知乎"))
    await started.wait()

    duplicate_message = await service.login(other, "知乎")

    assert duplicate_message == "知乎已有登录流程正在进行，请先取消或等待结束。"
    assert second_provider.closed is True
    assert await service.cancel(other) == "当前私聊没有进行中的平台登录。"
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None
    assert first_provider.closed is True


@pytest.mark.asyncio
async def test_zhihu_login_restores_cookie_when_save_fails():
    config = FailingSavingConfig(zhihu_cookies="z_c0=previous-secret")

    class SuccessfulZhihuProvider(FakeLoginProvider):
        display_name = "知乎"
        cookie_config_key = "zhihu_cookies"

    provider = SuccessfulZhihuProvider(
        [LoginPollResult(LoginPollState.SUCCESS, "z_c0=new-secret")]
    )
    service = AuthenticationService(
        config,
        provider_factories={"知乎": lambda: provider},
    )

    message = await service.login(FakeEvent(), "知乎")

    assert message == ("登录失败｜平台：知乎｜原因：Cookies 保存失败，原配置未被修改。")
    assert config["zhihu_cookies"] == "z_c0=previous-secret"
    assert config.save_calls == 1
    assert provider.closed is True


@pytest.mark.asyncio
async def test_zhihu_logout_clears_cookie_and_save_failure_rolls_back():
    config = FailingSavingConfig(zhihu_cookies="z_c0=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"知乎": lambda: None},
    )

    message = await service.logout("知乎")

    assert message == "Cookies 保存失败，原配置未被修改。"
    assert config["zhihu_cookies"] == "z_c0=session-secret"
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_zhihu_logout_clears_cookie_and_saves_config():
    config = SavingConfig(zhihu_cookies="z_c0=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"知乎": lambda: None},
    )

    message = await service.logout("知乎")

    assert message == "知乎已退出登录，Cookies 已清除。"
    assert config["zhihu_cookies"] == ""
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_zhihu_close_releases_active_login_on_plugin_unload():
    started = asyncio.Event()

    class WaitingZhihuProvider(FakeLoginProvider):
        display_name = "知乎"
        cookie_config_key = "zhihu_cookies"

        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    provider = WaitingZhihuProvider([])
    service = AuthenticationService(
        {},
        provider_factories={"知乎": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 60
    login_task = asyncio.create_task(service.login(FakeEvent(), "知乎"))
    await started.wait()

    await service.close()

    assert await login_task is None
    assert provider.closed is True


@pytest.mark.asyncio
async def test_weibo_same_platform_login_is_exclusive_and_logout_rolls_back():
    started = asyncio.Event()

    class WaitingWeiboProvider(FakeWeiboLoginProvider):
        async def poll_qr_status(self, session_key):
            started.set()
            return LoginPollResult(LoginPollState.WAITING)

    first_provider = WaitingWeiboProvider([])
    second_provider = WaitingWeiboProvider([])
    providers = [first_provider, second_provider]
    service = AuthenticationService(
        {},
        provider_factories={"微博": lambda: providers.pop(0)},
    )
    service.POLL_INTERVAL_SECONDS = 60
    owner = FakeEvent("adapter:private:owner")
    login_task = asyncio.create_task(service.login(owner, "微博"))
    await started.wait()

    assert await service.login(FakeEvent(), "微博") == (
        "微博已有登录流程正在进行，请先取消或等待结束。"
    )
    assert second_provider.closed is True
    assert await service.cancel(owner) == "已取消当前私聊中的平台登录。"
    assert await login_task is None

    config = FailingSavingConfig(weibo_cookies="SUB=session-secret")
    logout_service = AuthenticationService(
        config,
        provider_factories={"微博": lambda: None},
    )
    assert await logout_service.logout("微博") == "Cookies 保存失败，原配置未被修改。"
    assert config["weibo_cookies"] == "SUB=session-secret"


@pytest.mark.asyncio
async def test_weibo_login_restores_cookie_when_save_fails():
    config = FailingSavingConfig(weibo_cookies="SUB=original-secret")
    provider = FakeWeiboLoginProvider(
        [LoginPollResult(LoginPollState.SUCCESS, "SUB=new-secret")]
    )
    service = AuthenticationService(
        config,
        provider_factories={"微博": lambda: provider},
    )
    service.POLL_INTERVAL_SECONDS = 0

    message = await service.login(FakeEvent(), "微博")

    assert message == ("登录失败｜平台：微博｜原因：Cookies 保存失败，原配置未被修改。")
    assert config["weibo_cookies"] == "SUB=original-secret"
    assert provider.closed is True


@pytest.mark.asyncio
async def test_weibo_logout_clears_cookie_and_saves_config():
    config = SavingConfig(weibo_cookies="SUB=session-secret")
    service = AuthenticationService(
        config,
        provider_factories={"微博": lambda: None},
    )

    assert await service.logout("微博") == "微博已退出登录，Cookies 已清除。"
    assert config["weibo_cookies"] == ""
    assert config.save_calls == 1


@pytest.mark.asyncio
async def test_redbook_login_restores_cookie_when_save_fails():
    config = FailingSavingConfig(redbook_cookies="web_session=previous-session")
    provider = FakeLoginProvider(
        [LoginPollResult(LoginPollState.SUCCESS, "web_session=new-session")]
    )
    provider.display_name = "小红书"
    provider.cookie_config_key = "redbook_cookies"
    service = AuthenticationService(
        config,
        provider_factories={"小红书": lambda: provider},
    )

    message = await service.login(FakeEvent(), "小红书")

    assert message == (
        "登录失败｜平台：小红书｜原因：Cookies 保存失败，原配置未被修改。"
    )
    assert config["redbook_cookies"] == "web_session=previous-session"
    assert config.save_calls == 1
    assert provider.closed is True


@pytest.mark.asyncio
async def test_status_and_platform_names_only_accept_chinese():
    service = AuthenticationService(
        {"bilibili_cookies": ""},
        provider_factories={"B站": lambda: SimpleNamespace()},
    )

    assert await service.status() == "平台登录状态：\n- B站：未配置"
    assert "暂不支持“bilibili”" in service._unsupported_platform_message("bilibili")


@pytest.mark.asyncio
async def test_status_outputs_current_users_without_exposing_cookies():
    bilibili = FakeLoginProvider(
        [],
        user=PlatformUser(user_id="12345", display_name="测试用户"),
    )
    weibo = FakeWeiboLoginProvider([], user_error=RuntimeError("network failure"))
    config = {
        "cookies": {
            "bilibili_cookies": "SESSDATA=bilibili-secret",
            "weibo_cookies": "SUB=weibo-secret",
        }
    }
    service = AuthenticationService(
        config,
        provider_factories={"B站": lambda: bilibili, "微博": lambda: weibo},
    )

    message = await service.status()

    assert message == (
        "平台登录状态：\n"
        "- B站：已配置｜当前用户：测试用户（UID：12345）\n"
        "- 微博：已配置｜用户信息获取失败"
    )
    assert bilibili.user_cookie_headers == ["SESSDATA=bilibili-secret"]
    assert weibo.user_cookie_headers == ["SUB=weibo-secret"]
    assert bilibili.closed is True
    assert weibo.closed is True
    assert "bilibili-secret" not in message
    assert "weibo-secret" not in message


@pytest.mark.asyncio
async def test_login_succeeds_when_user_lookup_fails():
    provider = FakeLoginProvider(
        [LoginPollResult(LoginPollState.SUCCESS, "SESSDATA=session-secret")],
        user_error=RuntimeError("network failure"),
    )
    service = AuthenticationService(
        {"cookies": {"bilibili_cookies": ""}},
        provider_factories={"B站": lambda: provider},
    )

    message = await service.login(FakeEvent(), "B站")

    assert message == "B站登录成功，Cookies 已保存。当前用户信息获取失败。"
    assert "session-secret" not in message
