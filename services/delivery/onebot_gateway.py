"""OneBot 客户端调用适配。"""

from astrbot.api.event import AstrMessageEvent


class OneBotGateway:
    """兼容 AstrBot 不同 OneBot 客户端方法名的最小适配器。"""

    @staticmethod
    async def call(event: AstrMessageEvent, action: str, **params):
        bot = getattr(event, "bot", None)
        if bot and hasattr(bot, "call_action"):
            return await bot.call_action(action, **params)
        if bot and hasattr(bot, "call_api"):
            return await bot.call_api(action, **params)
        raise RuntimeError("当前事件没有可用的 OneBot 客户端")
