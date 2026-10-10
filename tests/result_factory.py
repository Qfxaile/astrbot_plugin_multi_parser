"""测试数据工厂：把旧测试写法转换为新的领域对象构造。"""

from pathlib import Path

from astrbot_multi_parser.core.contracts import (
    ContentDocument,
    DeliveryHints,
    MediaBundle,
    OrderedContent,
    ParseDiagnostics,
    ParseResult,
)
from astrbot_multi_parser.core.media import (
    TemporaryFileRegistry,
    cleanup_temporary_files,
)
from astrbot_multi_parser.core.rendering import ParseResultRenderer


class TestResult(ParseResult):
    """仅供迁移期间测试使用的结果对象，不属于插件运行时 API。"""

    def info_chain(self, **kwargs):
        return ParseResultRenderer.info_chain(self, **kwargs)

    def video_chain(self):
        return ParseResultRenderer.video_chain(self)

    def audio_chain(self):
        return ParseResultRenderer.audio_chain(self)

    def cleanup_temporary_files(self):
        cleanup_temporary_files(self)


def build_result(
    platform: str,
    *,
    content: ContentDocument | None = None,
    media: MediaBundle | None = None,
    video_url: str = "",
    video_download_headers: dict[str, str] | None = None,
    video_download_host_suffixes: tuple[str, ...] | None = None,
    title: str | None = None,
    author: str | None = None,
    description: str | None = None,
    cover_urls: list[str] | None = None,
    image_urls: list[str] | None = None,
    extra_lines: list[str] | None = None,
    ordered_contents: list[OrderedContent] | None = None,
    temporary_files: list[Path] | None = None,
    image_source_urls: dict[str, str] | None = None,
    image_download_headers: dict[str, str] | None = None,
    diagnostics: ParseDiagnostics | None = None,
    delivery: DeliveryHints | None = None,
) -> TestResult:
    if content is None:
        content = ContentDocument(
            title=title or "",
            author=author or "",
            description=description or "",
            cover_urls=cover_urls or [],
            image_urls=image_urls or [],
            extra_lines=extra_lines or [],
            ordered_contents=ordered_contents or [],
        )
    if media is None:
        media = MediaBundle()
    media.video_url = video_url or media.video_url
    if video_download_headers:
        media.video_download_headers.update(video_download_headers)
    if video_download_host_suffixes:
        media.video_download_host_suffixes = video_download_host_suffixes
    if image_source_urls:
        media.image_source_urls.update(image_source_urls)
    if image_download_headers:
        media.image_download_headers.update(image_download_headers)
    result = TestResult(
        platform=platform,
        content=content,
        media=media,
        diagnostics=diagnostics or ParseDiagnostics(),
        delivery=delivery or DeliveryHints(),
    )
    for path in temporary_files or []:
        TemporaryFileRegistry.register(result, path)
    return result
