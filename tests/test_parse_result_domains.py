from pathlib import Path

from astrbot_multi_parser.core.contracts import ParseDiagnostics
from astrbot_multi_parser.core.media import TemporaryFileRegistry
from result_factory import build_result


def test_parse_result_domain_views_share_storage():
    result = build_result(
        platform="测试",
        title="标题",
        image_urls=["https://example.test/image.jpg"],
        diagnostics=ParseDiagnostics(error="请求失败"),
        temporary_files=[Path("/tmp/image.jpg")],
    )

    assert result.content.title == "标题"
    assert result.content.image_urls == ["https://example.test/image.jpg"]
    assert TemporaryFileRegistry.paths(result) == (Path("/tmp/image.jpg"),)
    assert result.diagnostics.error == "请求失败"

    result.content.title = "新标题"
    result.media.video_url = "https://example.test/video.mp4"
    result.diagnostics.error = "新错误"

    assert result.content.title == "新标题"
    assert result.media.video_url.endswith("video.mp4")
    assert result.diagnostics.error == "新错误"
    assert not hasattr(result, "video_url")


def test_parse_result_rejects_unknown_constructor_fields():
    try:
        build_result(platform="测试", unknown_field="value")
    except TypeError:
        pass
    else:
        raise AssertionError("unknown ParseResult fields must be rejected")


def test_media_bundle_exposes_request_headers():
    result = build_result(platform="测试")
    result.media.image_download_headers["Referer"] = "https://example.test/"

    assert result.media.image_download_headers == {"Referer": "https://example.test/"}
