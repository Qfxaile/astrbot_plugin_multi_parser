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


class ContentDocument:
    """解析内容领域视图，统一管理标题、正文和图片顺序。"""

    def __init__(self, result: "ParseResult") -> None:
        self._result = result

    @property
    def title(self) -> str:
        return self._result.title

    @title.setter
    def title(self, value: str) -> None:
        self._result.title = value

    @property
    def author(self) -> str:
        return self._result.author

    @author.setter
    def author(self, value: str) -> None:
        self._result.author = value

    @property
    def description(self) -> str:
        return self._result.description

    @description.setter
    def description(self, value: str) -> None:
        self._result.description = value

    @property
    def extra_lines(self) -> list[str]:
        return self._result.extra_lines

    @property
    def ordered_contents(self) -> list[OrderedContent]:
        return self._result.ordered_contents

    @property
    def cover_urls(self) -> list[str]:
        return self._result.cover_urls

    @property
    def image_urls(self) -> list[str]:
        return self._result.image_urls


class MediaBundle:
    """解析媒体领域视图，集中暴露媒体地址与请求元数据。"""

    def __init__(self, result: "ParseResult") -> None:
        self._result = result

    @property
    def video_url(self) -> str:
        return self._result.video_url

    @video_url.setter
    def video_url(self, value: str) -> None:
        self._result.video_url = value

    @property
    def audio_url(self) -> str:
        return self._result.audio_url

    @audio_url.setter
    def audio_url(self, value: str) -> None:
        self._result.audio_url = value

    @property
    def image_urls(self) -> list[str]:
        return self._result.image_urls

    @property
    def cover_urls(self) -> list[str]:
        return self._result.cover_urls

    @property
    def temporary_files(self) -> list[Path]:
        return self._result.temporary_files

    @property
    def image_source_urls(self) -> dict[str, str]:
        return self._result.image_source_urls

    @property
    def image_download_headers(self) -> dict[str, str]:
        return self._result.image_download_headers

    @property
    def video_download_headers(self) -> dict[str, str]:
        return self._result.video_download_headers

    @video_download_headers.setter
    def video_download_headers(self, value: dict[str, str]) -> None:
        self._result.video_download_headers = value

    @property
    def video_download_host_suffixes(self) -> tuple[str, ...]:
        return self._result.video_download_host_suffixes

    @video_download_host_suffixes.setter
    def video_download_host_suffixes(self, value: tuple[str, ...]) -> None:
        self._result.video_download_host_suffixes = value


class ParseDiagnostics:
    """解析诊断领域视图，隔离错误和部分媒体失败信息。"""

    def __init__(self, result: "ParseResult") -> None:
        self._result = result

    @property
    def error(self) -> str:
        return self._result.error

    @error.setter
    def error(self, value: str) -> None:
        self._result.error = value

    @property
    def image_errors(self) -> dict[int, str]:
        return self._result.image_errors


@dataclass(frozen=True)
class MediaMetadata:
    """解析结果携带的媒体请求与临时文件元数据视图。"""

    temporary_files: list[Path]
    image_source_urls: dict[str, str]
    image_download_headers: dict[str, str]
    video_download_headers: dict[str, str]
    video_download_host_suffixes: tuple[str, ...]


@dataclass
class ParseResult:
    platform: str
    title: str = ""
    author: str = ""
    description: str = ""
    cover_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    video_url: str = ""
    error: str = ""
    extra_lines: list[str] = field(default_factory=list)
    ordered_contents: list[OrderedContent] = field(default_factory=list)
    image_errors: dict[int, str] = field(default_factory=dict)
    temporary_files: list[Path] = field(default_factory=list, repr=False)
    image_source_urls: dict[str, str] = field(default_factory=dict, repr=False)
    image_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_headers: dict[str, str] = field(default_factory=dict, repr=False)
    video_download_host_suffixes: tuple[str, ...] = field(
        default_factory=tuple, repr=False
    )
    disable_onebot_forward: bool = False
    split_media_for_onebot: bool = False
    keep_video_in_forward: bool = False
    audio_url: str = ""
    subtitle_text: str = ""
    subtitle_language: str = ""

    @property
    def content(self) -> ContentDocument:
        """返回内容领域视图，兼容旧字段的原地修改。"""
        return ContentDocument(self)

    @property
    def media(self) -> MediaBundle:
        """返回媒体领域视图，媒体基础设施优先使用此入口。"""
        return MediaBundle(self)

    @property
    def diagnostics(self) -> ParseDiagnostics:
        """返回解析诊断视图。"""
        return ParseDiagnostics(self)

    @property
    def image_count(self) -> int:
        return (
            len(self.cover_urls)
            + len(self.image_urls)
            + sum(
                item.kind in {"image", "image_error"} for item in self.ordered_contents
            )
        )

    @property
    def content_lines(self) -> list[str]:
        """返回供总结和会话历史使用的可见文本，保留正文顺序。"""
        lines = [self.description, *self.extra_lines]
        if self.ordered_contents:
            lines.extend(
                item.value
                for item in self.ordered_contents
                if item.value and item.kind in {"text", "image_error"}
            )
        return [line for line in lines if line]

    @property
    def image_references(self) -> list[str]:
        """返回按展示顺序排列的图片引用。"""
        if self.ordered_contents:
            return [
                item.value
                for item in self.ordered_contents
                if item.kind == "image" and item.value
            ]
        return [value for value in [*self.cover_urls, *self.image_urls] if value]

    @property
    def media_metadata(self) -> MediaMetadata:
        """返回媒体基础设施使用的元数据视图，保持与内容字段分离。"""
        bundle = self.media
        return MediaMetadata(
            temporary_files=bundle.temporary_files,
            image_source_urls=bundle.image_source_urls,
            image_download_headers=bundle.image_download_headers,
            video_download_headers=bundle.video_download_headers,
            video_download_host_suffixes=bundle.video_download_host_suffixes,
        )

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
