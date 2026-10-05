"""解析结果可见文本中的链接过滤。"""

from collections.abc import Mapping

from astrbot.api.message_components import Plain

from .delivery_policy import DeliveryPolicy
from .text_processing import replace_links


class LinkFilter:
    """只处理插件生成的 Plain 文本，不修改媒体组件。"""

    DEFAULT_TEXT = "[详细内容请打开原链接查看]"

    def __init__(self, config: Mapping[str, object]):
        self.policy = DeliveryPolicy(config)

    def apply(self, components: list) -> list:
        if not self.policy.filter_links_enabled():
            return components
        replacement = self.policy.filtered_link_text(self.DEFAULT_TEXT)
        return [
            Plain(replace_links(component.text, replacement))
            if isinstance(component, Plain)
            else component
            for component in components
        ]
