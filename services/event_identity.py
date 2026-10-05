"""AstrBot 事件中的平台、原始消息和发送者身份适配。"""

from collections.abc import Mapping

from astrbot.api.event import AstrMessageEvent


class EventIdentity:
    """集中处理事件对象的兼容读取，不包含消息发送逻辑。"""

    @staticmethod
    def raw_message(event: AstrMessageEvent):
        return getattr(event.message_obj, "raw_message", None)

    @classmethod
    def message_id(cls, event: AstrMessageEvent) -> str:
        raw = cls.raw_message(event) or {}
        message_id = raw.get("message_id") if isinstance(raw, dict) else ""
        fallback = getattr(event.message_obj, "message_id", "")
        return str(message_id or fallback or "")

    @staticmethod
    def platform_name(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_platform_name() or "")
        except Exception:
            return ""

    @classmethod
    def sender_identity(
        cls,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        sender_id = str(event.get_sender_id() or "0")
        try:
            public_name = event.get_sender_name()
        except Exception:
            public_name = ""
        sender_name = str(public_name) if public_name else sender_id
        raw = cls.raw_message(event)
        raw_sender = raw.get("sender") or {} if isinstance(raw, Mapping) else {}
        if isinstance(raw_sender, Mapping):
            raw_name = raw_sender.get("card")
            if prefer_raw_nickname:
                raw_name = raw_name or raw_sender.get("nickname")
            if raw_name:
                sender_name = str(raw_name)
        return sender_name, sender_id
