from astrbot_multi_parser.services.video_fallback import VideoFallbackService
from result_factory import build_result


def test_video_fallback_file_name_sanitizes_title_and_suffix():
    result = build_result(
        platform="test",
        title="bad:/title",
        video_url="https://example.com/video.unknown",
    )

    assert VideoFallbackService.file_name(result) == "bad__title.mp4"
