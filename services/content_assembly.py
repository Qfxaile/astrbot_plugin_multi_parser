"""消息组件和合并转发节点的纯组装工具。"""

from astrbot.api.message_components import Node, Plain


class ContentAssembler:
    """合并相邻文本并均衡拆分转发节点。"""

    FORWARD_NODE_LIMIT = 100

    @classmethod
    def merge_adjacent_plain(cls, components: list) -> list:
        merged: list = []
        for component in components:
            if (
                isinstance(component, Plain)
                and merged
                and isinstance(merged[-1], Plain)
            ):
                previous = merged[-1]
                merged[-1] = Plain(cls.join_plain_text(previous.text, component.text))
            else:
                merged.append(component)
        return merged

    @staticmethod
    def join_plain_text(previous: str, current: str) -> str:
        previous = previous.rstrip("\r\n")
        current = current.lstrip("\r\n")
        if not previous:
            return current
        if not current:
            return previous
        return f"{previous}\n{current}"

    @classmethod
    def balanced_forward_batches(cls, nodes: list[Node]) -> list[list[Node]]:
        if not nodes:
            return []
        batch_count = (
            len(nodes) + cls.FORWARD_NODE_LIMIT - 1
        ) // cls.FORWARD_NODE_LIMIT
        batch_size = (len(nodes) + batch_count - 1) // batch_count
        return [
            nodes[index : index + batch_size]
            for index in range(0, len(nodes), batch_size)
        ]
