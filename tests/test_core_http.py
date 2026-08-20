import httpx
import pytest
from astrbot_multi_parser.core.http import (
    CookieAccessError,
    ProxyConfigurationError,
    build_cookie_access_error,
    build_cookies,
    cookie_header_from_jar,
    host_matches,
    http_client_proxy_options,
    is_trusted_https_url,
    parse_cookie_header,
    raise_for_cookie_access,
    request_timeout,
)
from astrbot_multi_parser.core.parser import BaseParser


def test_parse_cookie_header_keeps_values_containing_equals():
    assert parse_cookie_header("a=1; invalid; b=two=2; =ignored") == [
        ("a", "1"),
        ("b", "two=2"),
    ]


def test_build_cookies_scopes_each_pair_to_all_domains():
    cookies = build_cookies("a=1; b=2", (".a.test", ".b.test"))

    scoped = {(item.name, item.value, item.domain) for item in cookies.jar}
    assert scoped == {
        ("a", "1", ".a.test"),
        ("a", "1", ".b.test"),
        ("b", "2", ".a.test"),
        ("b", "2", ".b.test"),
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://login.example.com/path", True),
        ("https://example.com/path", True),
        ("https://example.com.evil.test/path", False),
        ("https://user@example.com/path", False),
        ("https://example.com:444/path", False),
        ("http://example.com/path", False),
        ("https://[invalid/path", False),
    ],
)
def test_is_trusted_https_url_enforces_origin_boundary(url, expected):
    assert is_trusted_https_url(url, ("example.com",)) is expected


def test_host_matches_requires_full_label_boundary():
    assert host_matches("sub.example.com", ("example.com",)) is True
    assert host_matches("notexample.com", ("example.com",)) is False


def test_cookie_header_from_jar_filters_name_domain_and_unsafe_value():
    cookies = httpx.Cookies()
    cookies.set("session", "valid", domain=".example.com", path="/")
    cookies.set("ignored", "value", domain=".example.com", path="/")
    cookies.set("session", "other", domain=".evil.test", path="/")
    cookies.set("csrf", "bad;value", domain=".example.com", path="/")

    header = cookie_header_from_jar(
        cookies.jar,
        ("session", "csrf"),
        domain_allowed=lambda domain: host_matches(domain, ("example.com",)),
    )

    assert header == "session=valid"


def test_cookie_header_from_jar_requires_declared_credentials():
    cookies = httpx.Cookies()
    cookies.set("session", "valid", domain=".example.com", path="/")

    assert (
        cookie_header_from_jar(
            cookies.jar,
            ("session", "csrf"),
            required_names=("session", "csrf"),
            domain_allowed=lambda domain: host_matches(domain, ("example.com",)),
        )
        == ""
    )


def test_request_timeout_accepts_numeric_config():
    assert request_timeout({"request_timeout_seconds": "12.5"}) == 12.5
    assert request_timeout({}) == 30.0


@pytest.mark.parametrize(
    "proxy_url",
    [
        "http://proxy.example.com:8080",
        "https://user:password@proxy.example.com:8443",
    ],
)
def test_proxy_options_apply_enabled_platform_proxy(proxy_url):
    config = {
        "proxy_url": proxy_url,
        "proxy_switches": {"pixiv": True, "github": False},
    }

    assert http_client_proxy_options(config, "pixiv") == {
        "proxy": proxy_url,
        "trust_env": False,
    }
    assert http_client_proxy_options(config, "github") == {"trust_env": False}


def test_proxy_options_require_explicit_boolean_platform_switch():
    config = {
        "proxy_url": "http://proxy.example.com:8080",
        "proxy_switches": {"pixiv": "true"},
    }

    assert http_client_proxy_options(config, "pixiv") == {"trust_env": False}


@pytest.mark.parametrize(
    "proxy_url",
    [
        "",
        "socks5://proxy.example.com:1080",
        "http://proxy.example.com:invalid",
        "http://user:top-secret@proxy.example.com/path",
        "http://proxy.example.com:8080/?token=top-secret",
    ],
)
def test_proxy_options_reject_invalid_enabled_proxy_without_leaking_value(proxy_url):
    config = {
        "proxy_url": proxy_url,
        "proxy_switches": {"pixiv": True},
    }

    with pytest.raises(ProxyConfigurationError) as error:
        http_client_proxy_options(config, "pixiv")

    if proxy_url:
        assert proxy_url not in str(error.value)


def test_base_parser_uses_its_platform_proxy_options():
    class TestParser(BaseParser):
        name = "pixiv"

    parser = TestParser(
        {
            "proxy_url": "http://proxy.example.com:8080",
            "proxy_switches": {"pixiv": True},
        }
    )

    assert parser.http_client_options == {
        "proxy": "http://proxy.example.com:8080",
        "trust_env": False,
    }


@pytest.mark.parametrize(
    ("cookie_value", "expected"),
    [
        ("", "可能需要配置 Cookies"),
        ("session=secret", "配置的 Cookies 可能已失效"),
    ],
)
def test_cookie_access_error_distinguishes_missing_and_stale_cookie(
    cookie_value, expected
):
    error = build_cookie_access_error("测试平台", cookie_value)

    assert expected in str(error)
    assert "secret" not in str(error)


@pytest.mark.parametrize("status_code", [401, 403])
def test_raise_for_cookie_access_handles_only_confirmed_statuses(status_code):
    response = httpx.Response(
        status_code,
        request=httpx.Request("GET", "https://example.com/content"),
    )

    with pytest.raises(CookieAccessError, match="可能需要配置 Cookies"):
        raise_for_cookie_access(
            response,
            platform="测试平台",
            cookie_value="",
        )


def test_raise_for_cookie_access_leaves_other_http_errors_to_caller():
    response = httpx.Response(
        404,
        request=httpx.Request("GET", "https://example.com/content"),
    )

    raise_for_cookie_access(
        response,
        platform="测试平台",
        cookie_value="session=secret",
    )


def test_base_parser_preserves_non_cookie_http_errors():
    class TestParser(BaseParser):
        display_name = "测试平台"
        cookie_config_key = "test_cookies"

    response = httpx.Response(
        404,
        request=httpx.Request("GET", "https://example.com/missing"),
    )

    with pytest.raises(httpx.HTTPStatusError, match="404"):
        TestParser({"test_cookies": "session=secret"}).raise_for_response_status(
            response
        )
