from astrbot_multi_parser.core.settings import PluginSettings


def test_settings_normalizes_types_and_invalid_values():
    settings = PluginSettings(
        {
            "enabled": "yes",
            "count": "bad",
            "timeout": "2.5",
            "mode": "INVALID",
            "platform_switches": {"pixiv": "yes", "github": False},
        }
    )

    assert settings.boolean("enabled") is True
    assert settings.integer("count", 4, minimum=0) == 4
    assert settings.decimal("timeout", 1.0, minimum=1.0) == 2.5
    assert settings.choice("mode", {"safe", "fast"}, "safe") == "safe"
    assert settings.platform_enabled("pixiv") is True
    assert settings.platform_enabled("github") is False


def test_parse_feature_switches_default_on_and_read_nested_values():
    settings = PluginSettings(
        {
            "platform_feature_switches": {
                "douyin": {"video": False, "image_text": "invalid"},
                "bilibili": [],
            }
        }
    )

    assert settings.parse_feature_enabled("douyin", "video") is False
    assert settings.parse_feature_enabled("douyin", "live") is True
    assert settings.parse_feature_enabled("douyin", "image_text") is True
    assert settings.parse_feature_enabled("bilibili", "video") is True
