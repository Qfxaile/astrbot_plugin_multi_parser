from dataclasses import dataclass, field
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
    image_source_urls: dict[str, str] = field(default_factory=dict, repr=False)
    image_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_host_suffixes: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ParseDiagnostics:
    """解析跳过、错误及部分媒体失败信息。"""

    error: str = ""
    image_errors: dict[int, str] = field(default_factory=dict)
    skipped: bool = False


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
    diagnostics: ParseDiagnostics = field(default_factory=ParseDiagnostics)
    delivery: DeliveryHints = field(default_factory=DeliveryHints)

    @classmethod
    def skipped(cls, platform: str) -> "ParseResult":
        """返回静默跳过结果，允许调用方继续检查后续候选。"""
        return cls(platform=platform, diagnostics=ParseDiagnostics(skipped=True))

    @property
    def image_count(self) -> int:
        """按展示槽位统计图片，包含已记录的失败位置但跳过缺失项。"""
        return len(self.image_slots)

    @property
    def visible_text_lines(self) -> list[str]:
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
    def image_slots(self) -> list[str]:
        """跳过缺失图片，按展示顺序保留图片与已记录失败的空占位。"""
        if self.content.ordered_contents:
            return [
                item.value if item.kind == "image" else ""
                for item in self.content.ordered_contents
                if item.kind == "image_error" or (item.kind == "image" and item.value)
            ]
        return [
            value
            for index, value in enumerate(
                [*self.content.cover_urls, *self.content.image_urls]
            )
            if value or index in self.diagnostics.image_errors
        ]

    @property
    def ordered_image_references(self) -> list[str]:
        """返回按展示顺序排列的图片引用。"""
        return [value for value in self.image_slots if value]
