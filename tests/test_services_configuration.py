from astrbot_multi_parser.services.configuration import build_parsers


def test_registry_order_is_stable():
    assert list(build_parsers({})) == [
        "bilibili",
        "douyin",
        "redbook",
        "tieba",
        "weibo",
        "wechat",
        "xiaoheihe",
        "zhihu",
        "pixiv",
    ]


def test_pixiv_is_disabled_when_switch_is_missing_or_false():
    parsers = build_parsers({})
    from astrbot_multi_parser.services.configuration import enabled_parsers

    assert "pixiv" not in [parser.name for parser in enabled_parsers({}, parsers)]
    assert "pixiv" in [
        parser.name
        for parser in enabled_parsers({"platform_switches": {"pixiv": True}}, parsers)
    ]
