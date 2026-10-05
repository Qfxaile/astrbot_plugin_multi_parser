from astrbot_multi_parser.core.contracts import ParseResult
from astrbot_multi_parser.services.video_fallback import VideoFallbackService


def test_video_fallback_file_name_sanitizes_title_and_suffix():
    result = ParseResult(
        platform="test",
        title="bad:/title",
        video_url="https://example.com/video.unknown",
    )

    assert VideoFallbackService.file_name(result) == "bad__title.mp4"
