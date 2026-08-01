from astrbot_multi_parser.services.configuration import build_parsers


def test_registry_order_is_stable():
    assert list(build_parsers({})) == [
        "douyin",
        "fanqie",
        "redbook",
        "tieba",
        "weibo",
        "wechat",
        "xiaoheihe",
        "zhihu",
        "github",
        "qzone",
        "pixiv",
        "bilibili",
    ]


def test_github_is_enabled_and_pixiv_is_disabled_when_switch_is_missing():
    parsers = build_parsers({})
    from astrbot_multi_parser.services.configuration import enabled_parsers

    enabled = [parser.name for parser in enabled_parsers({}, parsers)]
    assert "github" in enabled
    assert "pixiv" not in enabled
    assert "pixiv" in [
        parser.name
        for parser in enabled_parsers({"platform_switches": {"pixiv": True}}, parsers)
    ]


def test_disabled_commerce_parsers_are_not_built_even_with_stale_switches():
    parsers = build_parsers(
        {
            "platform_switches": {
                "taobao": True,
                "jd": True,
                "pinduoduo": True,
            }
        }
    )

    assert {"taobao", "jd", "pinduoduo"}.isdisjoint(parsers)
