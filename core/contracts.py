from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class ParseContext:
    text: str
    json_urls: list[str] = field(default_factory=list)
    json_previews: list[str] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        return "\n".join([self.text, *self.json_urls]).strip()


@dataclass
class OrderedContent:
    kind: Literal["text", "image", "image_error"]
    value: str


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

    @property
    def image_count(self) -> int:
        return (
            len(self.cover_urls)
            + len(self.image_urls)
            + sum(
                item.kind in {"image", "image_error"} for item in self.ordered_contents
            )
        )

    def info_chain(
        self,
        include_video_url: bool = False,
        include_summary: bool = True,
        include_content: bool = True,
    ) -> list:
        """构建 AstrBot 消息组件，保留旧版公开调用方式。"""
        from .rendering import render_info_chain

        return render_info_chain(
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
        from .rendering import render_video_chain

        return render_video_chain(self)

    def audio_chain(self) -> list:
        """构建音频消息组件。"""
        from .rendering import render_audio_chain

        return render_audio_chain(self)
