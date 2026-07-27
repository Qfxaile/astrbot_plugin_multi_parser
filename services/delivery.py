import asyncio
import base64
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Node, Nodes, Plain

from ..core.contracts import ParseResult
from .text_processing import replace_links


class DeliveryService:
    """封装 AstrBot 跨平台消息组件编排与平台特有增强。"""

    ONEBOT_PLATFORM = "aiocqhttp"
    FORWARD_NODE_PLATFORMS = {"aiocqhttp", "satori"}
    FORWARD_MODES = {"always", "threshold", "never"}
    DEFAULT_FORWARD_MODE = "threshold"
    DEFAULT_IMAGE_THRESHOLD = 2
    DEFAULT_TEXT_THRESHOLD = 200
    FORWARD_NODE_LIMIT = 100
    VIDEO_OVER_LIMIT_ACTIONS = {"notice", "direct_link", "group_file"}
    DEFAULT_VIDEO_OVER_LIMIT_ACTION = "direct_link"
    DEFAULT_FILTERED_LINK_TEXT = "[详细内容请打开原链接查看]"

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = config
        self._onebot_names: dict[str, str] = {}

    @staticmethod
    async def call_onebot(event: AstrMessageEvent, action: str, **params):
        bot = getattr(event, "bot", None)
        if bot and hasattr(bot, "call_action"):
            return await bot.call_action(action, **params)
        if bot and hasattr(bot, "call_api"):
            return await bot.call_api(action, **params)
        raise RuntimeError("当前事件没有可用的 OneBot 客户端")

    @staticmethod
    def raw_message(event: AstrMessageEvent):
        return getattr(event.message_obj, "raw_message", None)

    def message_id(self, event: AstrMessageEvent) -> str:
        raw = self.raw_message(event) or {}
        message_id = raw.get("message_id") if isinstance(raw, dict) else ""
        fallback = getattr(event.message_obj, "message_id", "")
        return str(message_id or fallback or "")

    async def react_success(self, event: AstrMessageEvent) -> None:
        if not bool(self.config.get("enable_parse_reaction", True)):
            return
        if self._platform_name(event) != self.ONEBOT_PLATFORM:
            return

        message_id = self.message_id(event)
        if not message_id:
            logger.info("解析成功表情回应失败: 未获取到 message_id")
            return

        action = str(self.config.get("reaction_action", "set_msg_emoji_like")).strip()
        emoji_id = str(self.config.get("reaction_emoji_id", "124")).strip()
        if not action or not emoji_id:
            return

        try:
            await self.call_onebot(
                event, action, message_id=int(message_id), emoji_id=emoji_id
            )
        except Exception as exc:
            logger.info(f"解析成功表情回应失败: {exc}")

    def build_content_results(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        *,
        include_video_url: bool,
    ) -> list:
        results, _ = self.build_content_delivery(
            event,
            result,
            include_video_url=include_video_url,
        )
        return results

    def build_content_delivery(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        *,
        include_video_url: bool,
        include_video: bool = False,
    ) -> tuple[list, bool]:
        info_chain = self._filter_output_links(
            result.info_chain(include_video_url=include_video_url)
        )
        if not info_chain:
            return [], False
        if self._should_split_onebot_content(event, result):
            return [event.chain_result([component]) for component in info_chain], False
        if not self._should_forward_content(event, result, info_chain):
            if self._platform_name(event) == self.ONEBOT_PLATFORM:
                info_chain = self._merge_adjacent_plain_components(info_chain)
            return [event.chain_result(info_chain)], False

        forward_components = list(info_chain)
        video_embedded = (
            include_video
            and bool(result.video_url)
            and (self._forward_mode() == "always" or result.keep_video_in_forward)
        )
        if video_embedded:
            forward_components.extend(result.video_chain())
        sender_name, sender_id = self.forward_node_identity(event)
        merged_components = self._merge_adjacent_plain_components(forward_components)
        nodes = [
            Node(content=[component], name=sender_name, uin=sender_id)
            for component in merged_components
        ]
        results = [
            event.chain_result([Nodes(batch)])
            for batch in self._balanced_forward_batches(nodes)
        ]
        return results, video_embedded

    @classmethod
    def _balanced_forward_batches(cls, nodes: list[Node]) -> list[list[Node]]:
        """均衡拆分超长转发，避免首批贴近上限而尾批过小。"""
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

    async def send_forward_results(
        self,
        event: AstrMessageEvent,
        results: list,
        parse_result: ParseResult,
    ) -> None:
        """发送合并转发，只有构建阶段超过节点上限时才会分批。"""
        sender_name, sender_id = await self.resolve_forward_node_identity(event)
        for result in results:
            chain = getattr(result, "chain", result)
            if len(chain) != 1 or not isinstance(chain[0], Nodes):
                raise ValueError("合并转发结果结构无效")
            nodes = chain[0].nodes
            for node in nodes:
                node.name = sender_name
                node.uin = sender_id
            if self._can_send_onebot_url_forward(
                event, nodes, parse_result.image_source_urls
            ):
                image_files = parse_result.image_source_urls
                if parse_result.image_download_headers:
                    image_files = await self._download_onebot_forward_images(
                        event,
                        nodes,
                        parse_result.image_source_urls,
                        parse_result.image_download_headers,
                    )
                messages = await self._serialize_onebot_nodes(
                    nodes, image_files
                )
                await self._send_onebot_forward_nodes(event, messages)
                continue
            await event.send(MessageChain([Nodes(nodes)]))

    @classmethod
    def _can_send_onebot_url_forward(
        cls,
        event: AstrMessageEvent,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
    ) -> bool:
        """仅在 aiocqhttp 的全部图片都有远程地址时绕过 Base64 序列化。"""
        if cls._platform_name(event) != cls.ONEBOT_PLATFORM:
            return False
        images = [
            component
            for node in nodes
            for component in node.content
            if isinstance(component, Image)
        ]
        return bool(images) and all(
            cls._remote_image_url(image, image_source_urls) for image in images
        )

    @classmethod
    async def _serialize_onebot_nodes(
        cls,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
    ) -> list[dict]:
        """构造使用远程图片 URL 的 OneBot 节点，避免 WebSocket 携带 Base64。"""
        messages = []
        for node in nodes:
            content = []
            for component in node.content:
                if isinstance(component, Image):
                    content.append(
                        {
                            "type": "image",
                            "data": {
                                "file": cls._remote_image_url(
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

    @classmethod
    async def _download_onebot_forward_images(
        cls,
        event: AstrMessageEvent,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> dict[str, str]:
        """让协议端携带防盗链请求头下载图片，并返回协议端本地路径映射。"""
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
                source_url = cls._remote_image_url(component, image_source_urls)
                if not source_url:
                    raise RuntimeError("NapCat 图片预下载缺少远程地址。")
                file_name = cls._remote_image_file_name(source_url, image_index)
                try:
                    response = await cls.call_onebot(
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
                    response = await cls.call_onebot(
                        event,
                        "download_file",
                        base64=await cls._local_image_base64(component),
                        name=file_name,
                    )
                file_path = cls._downloaded_file_path(response)
                if not file_path:
                    raise RuntimeError("NapCat 图片预下载未返回本地文件路径。")
                image_key = str(component.path or component.file or "")
                if not image_key:
                    raise RuntimeError("NapCat 图片预下载无法关联消息组件。")
                image_files[image_key] = file_path
        return image_files

    @staticmethod
    async def _local_image_base64(image: Image) -> str:
        image_path = str(image.path or "").strip()
        if not image_path:
            raise RuntimeError("NapCat 单图上传缺少本地图片路径。")
        try:
            image_bytes = await asyncio.to_thread(Path(image_path).read_bytes)
        except OSError as exc:
            raise RuntimeError("NapCat 单图上传无法读取本地图片。") from exc
        return base64.b64encode(image_bytes).decode("ascii")

    @staticmethod
    def _downloaded_file_path(response: object) -> str:
        payload = response
        if not isinstance(payload, Mapping):
            payload = getattr(response, "data", None)
        if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
            payload = payload["data"]
        if not isinstance(payload, Mapping):
            return ""
        return str(payload.get("file") or payload.get("path") or "").strip()

    @staticmethod
    def _remote_image_file_name(url: str, index: int) -> str:
        source_name = PurePosixPath(urlparse(url).path).name
        source_name = re.sub(r"[^0-9A-Za-z._-]", "_", source_name).strip("._")
        return source_name[-100:] or f"image-{index}.jpg"

    @staticmethod
    def _remote_image_url(image: Image, image_source_urls: Mapping[str, str]) -> str:
        if image.path and (source_url := image_source_urls.get(str(image.path))):
            return source_url
        image_file = str(image.file or "")
        if image_file.startswith(("http://", "https://")):
            return image_file
        return ""

    async def _send_onebot_forward_nodes(
        self, event: AstrMessageEvent, messages: list[dict]
    ) -> None:
        """将已序列化的 URL 节点直接交给 OneBot，避免 AstrBot 转为 Base64。"""
        raw = self.raw_message(event)
        raw = raw if isinstance(raw, dict) else {}
        routing = {"messages": messages}
        if self_id := raw.get("self_id"):
            routing["self_id"] = self_id

        if group_id := raw.get("group_id"):
            await self.call_onebot(
                event,
                "send_group_forward_msg",
                group_id=int(group_id),
                **routing,
            )
            return

        user_id = raw.get("user_id") or event.get_sender_id()
        await self.call_onebot(
            event,
            "send_private_forward_msg",
            user_id=int(user_id),
            **routing,
        )

    @staticmethod
    def is_forward_delivery(results: list) -> bool:
        if not results:
            return False
        chain = getattr(results[0], "chain", results[0])
        return len(chain) == 1 and isinstance(chain[0], Nodes)

    @classmethod
    def _merge_adjacent_plain_components(cls, components: list) -> list:
        """合并相邻文本并保留媒体边界与原始顺序。"""
        merged: list = []
        for component in components:
            if (
                isinstance(component, Plain)
                and merged
                and isinstance(merged[-1], Plain)
            ):
                previous = merged[-1]
                merged[-1] = Plain(cls._join_plain_text(previous.text, component.text))
                continue
            merged.append(component)
        return merged

    @staticmethod
    def _join_plain_text(previous: str, current: str) -> str:
        previous = previous.rstrip("\r\n")
        current = current.lstrip("\r\n")
        if not previous:
            return current
        if not current:
            return previous
        return f"{previous}\n{current}"

    def _should_forward_content(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        chain: list,
    ) -> bool:
        if not self._supports_forward_nodes(event):
            return False
        if (
            self._platform_name(event) == self.ONEBOT_PLATFORM
            and result.disable_onebot_forward
        ):
            return False

        mode = self._forward_mode()
        if mode == "always":
            return True
        if mode == "never":
            return False

        image_threshold = self._non_negative_int(
            self.config.get("forward_image_threshold", self.DEFAULT_IMAGE_THRESHOLD),
            self.DEFAULT_IMAGE_THRESHOLD,
        )
        text_threshold = self._non_negative_int(
            self.config.get("forward_text_threshold", self.DEFAULT_TEXT_THRESHOLD),
            self.DEFAULT_TEXT_THRESHOLD,
        )
        text_length = sum(
            len(component.text) for component in chain if isinstance(component, Plain)
        )
        return result.image_count > image_threshold or text_length > text_threshold

    @classmethod
    def _should_split_onebot_content(
        cls,
        event: AstrMessageEvent,
        result: ParseResult,
    ) -> bool:
        return (
            cls._platform_name(event) == cls.ONEBOT_PLATFORM
            and result.split_media_for_onebot
        )

    def _forward_mode(self) -> str:
        mode = (
            str(self.config.get("forward_mode", self.DEFAULT_FORWARD_MODE))
            .strip()
            .lower()
        )
        return mode if mode in self.FORWARD_MODES else self.DEFAULT_FORWARD_MODE

    @staticmethod
    def _non_negative_int(value: object, default: int) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return default

    async def send_forward_links(
        self, event: AstrMessageEvent, result: ParseResult, reason: str
    ) -> None:
        """按适配器能力发送视频链接，非转发平台降级为普通文本。"""
        sender_name, sender_id = await self.resolve_forward_node_identity(
            event,
            prefer_raw_nickname=True,
        )
        summary_lines = [
            f"{result.platform} 解析链接",
            f"标题: {result.title or '未命名内容'}",
        ]
        if result.author:
            summary_lines.append(f"作者: {result.author}")
        if reason:
            summary_lines.append(f"说明: {reason}")

        summary_text = "\n".join(summary_lines)
        video_text = f"视频直链:\n{result.video_url}"
        platform_name = self._platform_name(event)
        if platform_name != self.ONEBOT_PLATFORM and self._supports_forward_nodes(
            event
        ):
            message_nodes = [
                Node(content=[Plain(text)], name=sender_name, uin=sender_id)
                for text in (summary_text, video_text)
            ]
            await event.send(MessageChain([Nodes(message_nodes)]))
            return

        if platform_name != self.ONEBOT_PLATFORM:
            text = "\n".join([*summary_lines, f"视频链接: {result.video_url}"])
            await event.send(MessageChain([Plain(text)]))
            return

        # OneBot 原生接口允许发送文本节点，并能保留现有的群聊/私聊路由行为。
        nodes = [
            self._raw_forward_node(sender_name, sender_id, summary_text),
            self._raw_forward_node(sender_name, sender_id, video_text),
        ]
        raw = self.raw_message(event)
        raw = raw if isinstance(raw, dict) else {}
        group_id = raw.get("group_id")
        if group_id:
            await self.call_onebot(
                event,
                "send_group_forward_msg",
                group_id=int(group_id),
                messages=nodes,
            )
            return

        user_id = raw.get("user_id") or sender_id
        await self.call_onebot(
            event,
            "send_private_forward_msg",
            user_id=int(user_id),
            messages=nodes,
        )

    async def send_video_over_limit(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        reason: str,
    ) -> None:
        """按配置处理超限视频，并让群文件失败稳定降级为直链。"""
        action = self.video_over_limit_action()
        if action == "notice":
            await event.send(MessageChain([Plain(reason or "视频未直接发送。")]))
            return

        if action == "group_file" and self._onebot_group_id(event):
            try:
                group_id = self._onebot_group_id(event)
                await self.call_onebot(
                    event,
                    "upload_group_file",
                    group_id=group_id,
                    file=result.video_url,
                    name=self._video_file_name(result),
                )
                return
            except Exception as exc:
                # 协议端远程下载、文件限制和平台配额都可能失败，直链是最可靠的回退。
                logger.warning(
                    f"视频群文件发送失败，已降级为直链: {type(exc).__name__}"
                )

        await self.send_forward_links(event, result, reason)

    def video_over_limit_action(self) -> str:
        """读取视频超限处理方式，无效值按发送直链处理。"""
        value = (
            str(
                self.config.get(
                    "video_over_limit_action",
                    self.DEFAULT_VIDEO_OVER_LIMIT_ACTION,
                )
            )
            .strip()
            .lower()
        )
        if value in self.VIDEO_OVER_LIMIT_ACTIONS:
            return value
        return self.DEFAULT_VIDEO_OVER_LIMIT_ACTION

    def _onebot_group_id(self, event: AstrMessageEvent) -> int | None:
        if self._platform_name(event) != self.ONEBOT_PLATFORM:
            return None
        raw = self.raw_message(event)
        raw_group_id = raw.get("group_id") if isinstance(raw, dict) else None
        try:
            group_id = raw_group_id or event.get_group_id()
            return int(group_id) if group_id else None
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _video_file_name(result: ParseResult) -> str:
        base_name = (result.title or f"{result.platform}视频").strip()
        base_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base_name)
        base_name = base_name.strip(" ._")[:80] or "video"
        suffix = PurePosixPath(urlparse(result.video_url).path).suffix.lower()
        if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".flv", ".avi"}:
            suffix = ".mp4"
        return f"{base_name}{suffix}"

    def _filter_output_links(self, components: list) -> list:
        """仅过滤插件生成的可见文本，不改写媒体组件和主动发送的直链。"""
        if not bool(self.config.get("filter_output_links", False)):
            return components
        replacement = str(
            self.config.get("filtered_link_text", self.DEFAULT_FILTERED_LINK_TEXT)
            or self.DEFAULT_FILTERED_LINK_TEXT
        )
        return [
            Plain(replace_links(component.text, replacement))
            if isinstance(component, Plain)
            else component
            for component in components
        ]

    def forward_node_identity(
        self,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        """QQ 合并转发先使用已缓存名称或账号，其他平台沿用发送者身份。"""
        if self._platform_name(event) == self.ONEBOT_PLATFORM:
            try:
                bot_id = str(event.get_self_id() or "")
            except Exception:
                bot_id = ""

            if bot_id:
                return self._onebot_names.get(bot_id, bot_id), bot_id

        return self.sender_identity(
            event,
            prefer_raw_nickname=prefer_raw_nickname,
        )

    async def resolve_forward_node_identity(
        self,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        """发送前通过 OneBot 登录信息解析 QQ 昵称，并缓存到当前服务实例。"""
        sender_name, sender_id = self.forward_node_identity(
            event,
            prefer_raw_nickname=prefer_raw_nickname,
        )
        if self._platform_name(event) != self.ONEBOT_PLATFORM:
            return sender_name, sender_id
        try:
            bot_id = str(event.get_self_id() or "")
        except Exception:
            bot_id = ""
        if not bot_id:
            return sender_name, sender_id
        if bot_id in self._onebot_names:
            return self._onebot_names[bot_id], bot_id

        try:
            login_info = await self.call_onebot(
                event,
                "get_login_info",
                self_id=int(bot_id),
            )
            if isinstance(login_info, Mapping):
                bot_name = str(login_info.get("nickname") or "").strip()
                if bot_name:
                    self._onebot_names[bot_id] = bot_name
                    return bot_name, bot_id
        except Exception as exc:
            logger.info(f"获取 QQ 机器人名称失败: {type(exc).__name__}")

        return sender_name, bot_id

    def sender_identity(
        self,
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

        raw = self.raw_message(event)
        raw_sender = raw.get("sender") or {} if isinstance(raw, dict) else {}
        if isinstance(raw_sender, dict):
            raw_name = raw_sender.get("card")
            if prefer_raw_nickname:
                raw_name = raw_name or raw_sender.get("nickname")
            if raw_name:
                sender_name = str(raw_name)
        return sender_name, sender_id

    @classmethod
    def _supports_forward_nodes(cls, event: AstrMessageEvent) -> bool:
        return cls._platform_name(event) in cls.FORWARD_NODE_PLATFORMS

    @staticmethod
    def _platform_name(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_platform_name() or "")
        except Exception:
            return ""

    @staticmethod
    def _raw_forward_node(name: str, user_id: str, text: str) -> dict:
        return {
            "type": "node",
            "data": {
                "name": name,
                "uin": user_id,
                "content": [{"type": "text", "data": {"text": text}}],
            },
        }
