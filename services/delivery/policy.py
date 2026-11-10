"""消息投递策略的纯配置逻辑。"""

from collections.abc import Mapping
from dataclasses import dataclass

from ...core.settings import PluginSettings


@dataclass(frozen=True)
class DeliveryPolicy:
    """集中解释投递相关配置，不依赖 AstrBot 事件或消息组件。"""

    config: Mapping[str, object]
    default_forward_mode: str = "threshold"
    default_image_threshold: int = 2
    default_text_threshold: int = 200
    default_video_action: str = "direct_link"
    default_non_forward_message_limit: int = 3

    @property
    def settings(self) -> PluginSettings:
        return PluginSettings(self.config)

    def forward_mode(self) -> str:
        return self.settings.choice(
            "forward_mode", {"always", "threshold", "never"}, self.default_forward_mode
        )

    def should_forward(self, image_count: int, text_length: int) -> bool:
        mode = self.forward_mode()
        if mode == "always":
            return True
        if mode == "never":
            return False
        image_threshold = self.non_negative_int(
            self.settings.integer(
                "forward_image_threshold", self.default_image_threshold
            ),
            self.default_image_threshold,
        )
        text_threshold = self.non_negative_int(
            self.settings.integer(
                "forward_text_threshold", self.default_text_threshold
            ),
            self.default_text_threshold,
        )
        return image_count > image_threshold or text_length > text_threshold

    def video_over_limit_action(self) -> str:
        return self.settings.choice(
            "video_over_limit_action",
            {"notice", "direct_link", "group_file"},
            self.default_video_action,
        )

    def non_forward_message_limit(self) -> int:
        """返回非合并转发内容条数上限；-1 表示不限，0 表示不发送。"""
        value = self.settings.integer(
            "non_forward_message_limit", self.default_non_forward_message_limit
        )
        return value if value >= -1 else self.default_non_forward_message_limit

    def limit_private_chat_messages(self) -> bool:
        """返回是否将不支持合并转发的私聊也纳入条数限制。"""
        return self.settings.boolean("limit_private_chat_messages", False)

    def filter_links_enabled(self) -> bool:
        return self.settings.boolean("filter_output_links")

    def filtered_link_text(self, default: str) -> str:
        return self.settings.text("filtered_link_text", default) or default

    @staticmethod
    def non_negative_int(value: object, default: int) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return default
