from astrbot_multi_parser.core.contracts import OrderedContent
from result_factory import build_result


def test_parse_result_views_preserve_text_and_image_order():
    result = build_result(
        platform="test",
        description="简介",
        extra_lines=["附加"],
        ordered_contents=[
            OrderedContent("text", "正文"),
            OrderedContent("image", "https://img.example/a.jpg"),
            OrderedContent("image_error", "图片失败"),
        ],
    )

    assert result.visible_text_lines == ["简介", "附加", "正文", "图片失败"]
    assert result.ordered_image_references == ["https://img.example/a.jpg"]
