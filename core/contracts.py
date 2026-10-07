from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class ParseContext:
    text: str
    json_urls: list[str] = field(default_factory=list)
    json_previews: list[str] = field(default_factory=list)
    json_titles: list[str] = field(default_factory=list)
    json_metadata: list[dict[str, str]] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        return "\n".join([self.text, *self.json_urls]).strip()


@dataclass
class OrderedContent:
    kind: Literal["text", "image", "image_error"]
    value: str


@dataclass
class ContentDocument:
    """解析得到的可见文本与有序图片内容。"""

    title: str = ""
    author: str = ""
    description: str = ""
    cover_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    extra_lines: list[str] = field(default_factory=list)
    ordered_contents: list[OrderedContent] = field(default_factory=list)


@dataclass
class MediaBundle:
    """解析得到的视频、音频及媒体请求生命周期数据。"""

    video_url: str = ""
    audio_url: str = ""
    temporary_files: list[Path] = field(default_factory=list, repr=False)
    image_source_urls: dict[str, str] = field(default_factory=dict, repr=False)
    image_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_host_suffixes: tuple[str, ...] = field(default_factory=tuple)
    subtitle_text: str = ""
    subtitle_language: str = ""


@dataclass
class ParseDiagnostics:
    """解析错误及部分媒体失败信息。"""

    error: str = ""
    image_errors: dict[int, str] = field(default_factory=dict)


@dataclass
class DeliveryHints:
    """解析结果携带的协议投递提示。"""

    disable_onebot_forward: bool = False
    split_media_for_onebot: bool = False
    keep_video_in_forward: bool = False


@dataclass
class ParseResult:
    platform: str
    content: ContentDocument = field(default_factory=ContentDocument)
    media: MediaBundle = field(default_factory=MediaBundle)
    title: str = ""
    author: str = ""
    description: str = ""
    cover_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    video_url: str = ""
    extra_lines: list[str] = field(default_factory=list)
    ordered_contents: list[OrderedContent] = field(default_factory=list)
    diagnostics: ParseDiagnostics = field(default_factory=ParseDiagnostics)
    temporary_files: list[Path] = field(default_factory=list, repr=False)
    image_source_urls: dict[str, str] = field(default_factory=dict, repr=False)
    image_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_host_suffixes: tuple[str, ...] = field(
        default_factory=tuple, repr=False
    )
    delivery: DeliveryHints = field(default_factory=DeliveryHints)
    audio_url: str = ""
    subtitle_text: str = ""
    subtitle_language: str = ""

    def __post_init__(self) -> None:
        """将旧构造参数一次性装载到内容对象，后续读取统一走领域对象。"""
        if self.content == ContentDocument():
            self.content = ContentDocument(
                title=self.title,
                author=self.author,
                description=self.description,
                cover_urls=self.cover_urls,
                image_urls=self.image_urls,
                extra_lines=self.extra_lines,
                ordered_contents=self.ordered_contents,
            )
        if self.media == MediaBundle():
            self.media = MediaBundle(
                video_url=self.video_url,
                audio_url=self.audio_url,
                temporary_files=self.temporary_files,
                image_source_urls=self.image_source_urls,
                image_download_headers=self.image_download_headers,
                video_download_headers=self.video_download_headers,
                video_download_host_suffixes=self.video_download_host_suffixes,
                subtitle_text=self.subtitle_text,
                subtitle_language=self.subtitle_language,
            )

    @property
    def image_count(self) -> int:
        return (
            len(self.content.cover_urls)
            + len(self.content.image_urls)
            + sum(
                item.kind in {"image", "image_error"}
                for item in self.content.ordered_contents
            )
        )

    @property
    def content_lines(self) -> list[str]:
        """返回供总结和会话历史使用的可见文本，保留正文顺序。"""
        lines = [self.content.description, *self.content.extra_lines]
        if self.content.ordered_contents:
            lines.extend(
                item.value
                for item in self.content.ordered_contents
                if item.value and item.kind in {"text", "image_error"}
            )
        return [line for line in lines if line]

    @property
    def image_references(self) -> list[str]:
        """返回按展示顺序排列的图片引用。"""
        if self.content.ordered_contents:
            return [
                item.value
                for item in self.content.ordered_contents
                if item.kind == "image" and item.value
            ]
        return [
            value
            for value in [*self.content.cover_urls, *self.content.image_urls]
            if value
        ]

    def info_chain(
        self,
        include_video_url: bool = False,
        include_summary: bool = True,
        include_content: bool = True,
    ) -> list:
        """构建 AstrBot 消息组件，保留旧版公开调用方式。"""
        from .rendering import ParseResultRenderer

        return ParseResultRenderer.info_chain(
            self,
            include_video_url=include_video_url,
            include_summary=include_summary,
            include_content=include_content,
        )

    def cleanup_temporary_files(self) -> None:
        """清理本次解析创建的临时媒体文件。"""
        from .media import cleanup_temporary_files

        cleanup_temporary_files(self)

    def video_chain(self) -> list:
        """构建视频消息组件，保留旧版公开调用方式。"""
        from .rendering import ParseResultRenderer

        return ParseResultRenderer.video_chain(self)

    def audio_chain(self) -> list:
        """构建音频消息组件。"""
        from .rendering import ParseResultRenderer

        return ParseResultRenderer.audio_chain(self)
