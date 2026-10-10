"""OneBot 机器人身份查询与短期缓存。"""

from collections.abc import Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..event_identity import EventIdentity
from .onebot_gateway import OneBotGateway


class OneBotIdentityResolver:
    """解析合并转发使用的机器人昵称，并按机器人 ID 缓存结果。"""

    PLATFORM = "aiocqhttp"

    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    def cached(self, event: AstrMessageEvent) -> tuple[str, str] | None:
        bot_id = self._bot_id(event)
        if not bot_id or EventIdentity.platform_name(event) != self.PLATFORM:
            return None
        name = self._names.get(bot_id)
        return (name, bot_id) if name else (bot_id, bot_id)

    async def resolve(
        self,
        event: AstrMessageEvent,
        fallback: tuple[str, str],
    ) -> tuple[str, str]:
        cached = self.cached(event)
        if cached and cached[0] != cached[1]:
            return cached
        bot_id = self._bot_id(event)
        if not bot_id or EventIdentity.platform_name(event) != self.PLATFORM:
            return fallback
        try:
            payload = await OneBotGateway.call(
                event, "get_login_info", self_id=int(bot_id)
            )
            if isinstance(payload, Mapping):
                name = str(payload.get("nickname") or "").strip()
                if name:
                    self._names[bot_id] = name
                    return name, bot_id
        except Exception as exc:
            logger.info(f"获取 QQ 机器人名称失败: {type(exc).__name__}")
        return fallback[0], bot_id

    @staticmethod
    def _bot_id(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_self_id() or "")
        except Exception:
            return ""
