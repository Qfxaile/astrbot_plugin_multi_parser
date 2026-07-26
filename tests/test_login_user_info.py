import httpx
import pytest
from astrbot_multi_parser.core.platform_login import PlatformUser
from astrbot_multi_parser.platforms.bilibili.login import BilibiliLoginProvider
from astrbot_multi_parser.platforms.douyin.login import DouyinLoginProvider
from astrbot_multi_parser.platforms.redbook.login import RedBookLoginProvider
from astrbot_multi_parser.platforms.tieba.login import TiebaLoginProvider
from astrbot_multi_parser.platforms.wechat.login import WeChatLoginProvider
from astrbot_multi_parser.platforms.weibo.login import WeiboLoginProvider
from astrbot_multi_parser.platforms.xiaoheihe.login import XiaoheiheLoginProvider
from astrbot_multi_parser.platforms.zhihu.login import ZhihuLoginProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_type", "expected_host", "payload", "expected_user"),
    [
        (
            BilibiliLoginProvider,
            "api.bilibili.com",
            {"code": 0, "data": {"isLogin": True, "mid": 123, "uname": "B站用户"}},
            PlatformUser(user_id="123", display_name="B站用户"),
        ),
        (
            DouyinLoginProvider,
            "www.douyin.com",
            {"status_code": 0, "user": {"uid": "456", "nickname": "抖音用户"}},
            PlatformUser(user_id="456", display_name="抖音用户"),
        ),
        (
            TiebaLoginProvider,
            "tieba.baidu.com",
            {"data": {"user_id": 789, "user_name_show": "贴吧用户"}},
            PlatformUser(user_id="789", display_name="贴吧用户"),
        ),
        (
            WeiboLoginProvider,
            "weibo.com",
            {
                "ok": 1,
                "data": {"user": {"idstr": "101", "screen_name": "微博用户"}},
            },
            PlatformUser(user_id="101", display_name="微博用户"),
        ),
        (
            ZhihuLoginProvider,
            "www.zhihu.com",
            {"id": "202", "name": "知乎用户"},
            PlatformUser(user_id="202", display_name="知乎用户"),
        ),
    ],
)
async def test_http_login_providers_query_current_user_from_official_host(
    provider_type,
    expected_host,
    payload,
    expected_user,
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == expected_host
        assert request.headers["Cookie"] == "session=account-secret"
        return httpx.Response(200, request=request, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_type({}, client=client)
        user = await provider.get_current_user("session=account-secret")

    assert user == expected_user


@pytest.mark.asyncio
async def test_redbook_queries_signed_current_user_with_configured_cookies():
    class FakeSigner:
        def sign(self, method, uri, a1_value, payload):
            assert method == "GET"
            assert uri == RedBookLoginProvider.CURRENT_USER_URI
            assert a1_value == "device-secret"
            assert payload == {}
            return {"X-S": "signature", "X-T": "timestamp"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "edith.xiaohongshu.com"
        assert request.url.path == RedBookLoginProvider.CURRENT_USER_URI
        assert request.headers["X-S"] == "signature"
        assert "a1=device-secret" in request.headers["Cookie"]
        assert "web_session=session-secret" in request.headers["Cookie"]
        return httpx.Response(
            200,
            request=request,
            json={
                "code": 0,
                "data": {"basic_info": {"user_id": "303", "nickname": "小红书用户"}},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        user = await provider.get_current_user(
            "a1=device-secret; web_session=session-secret"
        )

    assert user == PlatformUser(user_id="303", display_name="小红书用户")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "cookie_header", "expected_user"),
    [
        (
            WeChatLoginProvider({}),
            "yb_user_id=wechat-user; yb_token=token-secret",
            PlatformUser(user_id="wechat-user"),
        ),
        (
            XiaoheiheLoginProvider({}),
            "pkey=session-secret; heybox_id=404",
            PlatformUser(user_id="404"),
        ),
    ],
)
async def test_token_login_providers_expose_only_non_secret_user_id(
    provider,
    cookie_header,
    expected_user,
):
    try:
        assert await provider.get_current_user(cookie_header) == expected_user
    finally:
        await provider.close()
