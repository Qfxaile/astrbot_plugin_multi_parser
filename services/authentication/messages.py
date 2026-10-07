"""平台登录命令的用户可见文案格式化。"""

from collections.abc import Iterable

from ...core.platform_login import (
    PlatformLoginError,
    PlatformLoginProvider,
    PlatformUser,
)


class LoginMessageFormatter:
    """集中生成登录状态、错误和用户信息文案。"""

    @staticmethod
    def user(user: PlatformUser) -> str:
        display_name = LoginMessageFormatter.user_field(user.display_name)
        user_id = LoginMessageFormatter.user_field(user.user_id)
        if display_name and user_id:
            return f"{display_name}（UID：{user_id}）"
        if display_name:
            return display_name
        return f"UID：{user_id}"

    @staticmethod
    def user_field(value: object) -> str:
        return " ".join(str(value or "").split())[:100]

    @staticmethod
    def unsupported(platform_name: str, supported: Iterable[str]) -> str:
        names = "、".join(supported)
        if not platform_name:
            return f"请提供平台中文名。当前支持：{names}。"
        return f"暂不支持“{platform_name}”登录。当前支持：{names}。"

    @staticmethod
    def error(platform_name: str, error: PlatformLoginError | str) -> str:
        detail = str(error).strip() or "发生未知错误，请稍后重试。"
        for prefix in (f"{platform_name}登录", platform_name):
            if detail.startswith(prefix):
                detail = detail[len(prefix) :].lstrip("：:，, ")
                break
        return f"登录失败｜平台：{platform_name}｜原因：{detail}"

    @staticmethod
    def expired(provider: PlatformLoginProvider) -> str:
        message = "二维码已过期，请重新发起登录。"
        if not provider.sms_fallback_available:
            message += "该平台短信登录需要额外人机验证，当前私聊流程暂不支持。"
        return LoginMessageFormatter.error(provider.display_name, message)
