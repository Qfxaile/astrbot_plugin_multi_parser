"""OneBot 合并转发节点发送路由。"""

from astrbot.api.event import AstrMessageEvent

from ..event_identity import EventIdentity
from .onebot_gateway import OneBotGateway


class OneBotForwardSender:
    """按事件原始路由将节点发送到群聊或私聊。"""

    @staticmethod
    async def send(event: AstrMessageEvent, messages: list[dict]) -> None:
        raw = EventIdentity.raw_message(event)
        raw = raw if isinstance(raw, dict) else {}
        routing = {"messages": messages}
        if self_id := raw.get("self_id"):
            routing["self_id"] = self_id

        if group_id := raw.get("group_id"):
            await OneBotGateway.call(
                event,
                "send_group_forward_msg",
                group_id=int(group_id),
                **routing,
            )
            return

        user_id = raw.get("user_id") or event.get_sender_id()
        await OneBotGateway.call(
            event,
            "send_private_forward_msg",
            user_id=int(user_id),
            **routing,
        )
