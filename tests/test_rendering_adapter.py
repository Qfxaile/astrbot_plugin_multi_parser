from astrbot_multi_parser.core import ParseResultRenderer
from result_factory import build_result


def test_parse_result_renderer_preserves_legacy_result_rendering():
    result = build_result(platform="test", title="标题")

    assert [item.text for item in ParseResultRenderer.info_chain(result)] == ["标题"]
    assert ParseResultRenderer.video_chain(result) == []
    assert ParseResultRenderer.audio_chain(result) == []
