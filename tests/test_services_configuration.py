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
        "taobao",
        "jd",
        "pinduoduo",
        "pixiv",
        "bilibili",
    ]


def test_pixiv_is_disabled_when_switch_is_missing_or_false():
    parsers = build_parsers({})
    from astrbot_multi_parser.services.configuration import enabled_parsers

    assert "pixiv" not in [parser.name for parser in enabled_parsers({}, parsers)]
    assert "pixiv" in [
        parser.name
        for parser in enabled_parsers({"platform_switches": {"pixiv": True}}, parsers)
    ]


def test_commerce_parsers_are_enabled_by_default_and_can_be_disabled():
    parsers = build_parsers({})
    from astrbot_multi_parser.services.configuration import enabled_parsers

    enabled = [parser.name for parser in enabled_parsers({}, parsers)]
    assert {"taobao", "jd", "pinduoduo"}.issubset(enabled)

    disabled = [
        parser.name
        for parser in enabled_parsers(
            {
                "platform_switches": {
                    "taobao": False,
                    "jd": False,
                    "pinduoduo": False,
                }
            },
            parsers,
        )
    ]
    assert {"taobao", "jd", "pinduoduo"}.isdisjoint(disabled)
