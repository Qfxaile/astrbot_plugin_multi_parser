from pathlib import Path

from astrbot_multi_parser.core.contracts import ParseDiagnostics, ParseResult


def test_parse_result_domain_views_share_legacy_storage():
    result = ParseResult(
        platform="测试",
        title="标题",
        image_urls=["https://example.test/image.jpg"],
        diagnostics=ParseDiagnostics(error="请求失败"),
        temporary_files=[Path("/tmp/image.jpg")],
    )

    assert result.content.title == "标题"
    assert result.content.image_urls == result.image_urls
    assert result.content.image_urls == result.image_urls
    assert result.media.temporary_files == result.media.temporary_files
    assert result.diagnostics.error == "请求失败"

    result.content.title = "新标题"
    result.media.video_url = "https://example.test/video.mp4"
    result.diagnostics.error = "新错误"

    assert result.content.title == "新标题"
    assert result.media.video_url.endswith("video.mp4")
    assert result.diagnostics.error == "新错误"


def test_media_bundle_exposes_request_headers():
    result = ParseResult(platform="测试")
    result.media.image_download_headers["Referer"] = "https://example.test/"

    assert result.media.image_download_headers == {"Referer": "https://example.test/"}
