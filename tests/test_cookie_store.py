import pytest
from astrbot_multi_parser.services.cookie_store import CookieStore


class Config(dict):
    def save_config(self):
        raise RuntimeError("save failed")


def test_cookie_store_reads_grouped_cookie_values():
    assert (
        CookieStore({"cookies": {"site": "session=value"}}).get("site")
        == "session=value"
    )


def test_cookie_store_rolls_back_when_persistence_fails():
    config = Config({"cookies": {"site": "old=value"}})

    with pytest.raises(ValueError, match="保存失败"):
        CookieStore(config).save("site", "new=value")

    assert config["cookies"]["site"] == "old=value"
