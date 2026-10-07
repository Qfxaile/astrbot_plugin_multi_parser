from astrbot_multi_parser.core.platform_login import PlatformUser
from astrbot_multi_parser.services.authentication.messages import LoginMessageFormatter


def test_login_message_formatter_sanitizes_user_fields():
    assert LoginMessageFormatter.user(PlatformUser(" 42 ", "  测试  用户 ")) == (
        "测试 用户（UID：42）"
    )
    assert "暂不支持" in LoginMessageFormatter.unsupported("平台", ["B站"])
