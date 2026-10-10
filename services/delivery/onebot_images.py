"""OneBot 合并转发图片预下载。"""

import asyncio
import base64
from collections.abc import Mapping
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Node

from .onebot_forward import OneBotForwardSerializer
from .onebot_gateway import OneBotGateway


class OneBotImageDownloader:
    """下载带防盗链图片，失败时回退到本地 Base64 上传。"""

    async def download(
        self,
        event: AstrMessageEvent,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> dict[str, str]:
        serialized_headers = [
            f"{name}={value}"
            for name, value in headers.items()
            if name
            and value
            and "\r" not in name + value
            and "\n" not in name + value
            and "=" not in name
        ]
        image_files: dict[str, str] = {}
        image_index = 0
        for node in nodes:
            for component in node.content:
                if not isinstance(component, Image):
                    continue
                image_index += 1
                source_url = OneBotForwardSerializer.remote_image_url(
                    component, image_source_urls
                )
                if not source_url:
                    raise RuntimeError("NapCat 图片预下载缺少远程地址。")
                file_name = OneBotForwardSerializer.remote_image_file_name(
                    source_url, image_index
                )
                try:
                    response = await OneBotGateway.call(
                        event,
                        "download_file",
                        url=source_url,
                        name=file_name,
                        headers=serialized_headers,
                    )
                except Exception as exc:
                    logger.info(
                        f"NapCat 第 {image_index} 张图片 URL 预下载失败，"
                        f"改用单图上传: {type(exc).__name__}"
                    )
                    response = await OneBotGateway.call(
                        event,
                        "download_file",
                        base64=await self.local_base64(component),
                        name=file_name,
                    )
                file_path = self.downloaded_file_path(response)
                if not file_path:
                    raise RuntimeError("NapCat 图片预下载未返回本地文件路径。")
                image_key = str(component.path or component.file or "")
                if not image_key:
                    raise RuntimeError("NapCat 图片预下载无法关联消息组件。")
                image_files[image_key] = file_path
        return image_files

    @staticmethod
    async def local_base64(image: Image) -> str:
        image_path = str(image.path or "").strip()
        if not image_path:
            raise RuntimeError("NapCat 单图上传缺少本地图片路径。")
        try:
            image_bytes = await asyncio.to_thread(Path(image_path).read_bytes)
        except OSError as exc:
            raise RuntimeError("NapCat 单图上传无法读取本地图片。") from exc
        return base64.b64encode(image_bytes).decode("ascii")

    @staticmethod
    def downloaded_file_path(response: object) -> str:
        payload = response
        if not isinstance(payload, Mapping):
            payload = getattr(response, "data", None)
        if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
            payload = payload["data"]
        if not isinstance(payload, Mapping):
            return ""
        return str(payload.get("file") or payload.get("path") or "").strip()
