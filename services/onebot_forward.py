"""OneBot 合并转发节点序列化工具。"""

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import urlparse

from astrbot.api.message_components import Image, Node


class OneBotForwardSerializer:
    """将 AstrBot 节点转换为 OneBot 原生节点结构。"""

    @staticmethod
    def remote_image_url(image: Image, image_source_urls: Mapping[str, str]) -> str:
        if image.path and (source_url := image_source_urls.get(str(image.path))):
            return source_url
        image_file = str(image.file or "")
        return image_file if image_file.startswith(("http://", "https://")) else ""

    @staticmethod
    def remote_image_file_name(url: str, index: int) -> str:
        source_name = PurePosixPath(urlparse(url).path).name
        source_name = re.sub(r"[^0-9A-Za-z._-]", "_", source_name).strip("._")
        return source_name[-100:] or f"image-{index}.jpg"

    @classmethod
    async def serialize(
        cls, nodes: list[Node], image_source_urls: Mapping[str, str]
    ) -> list[dict]:
        messages = []
        for node in nodes:
            content = []
            for component in node.content:
                if isinstance(component, Image):
                    content.append(
                        {
                            "type": "image",
                            "data": {
                                "file": cls.remote_image_url(
                                    component, image_source_urls
                                )
                            },
                        }
                    )
                else:
                    content.append(await component.to_dict())
            messages.append(
                {
                    "type": "node",
                    "data": {
                        "user_id": str(node.uin),
                        "nickname": node.name,
                        "content": content,
                    },
                }
            )
        return messages
