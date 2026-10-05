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
