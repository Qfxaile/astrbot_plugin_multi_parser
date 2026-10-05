from astrbot_multi_parser.core.contracts import OrderedContent, ParseResult


def test_parse_result_views_preserve_text_and_image_order():
    result = ParseResult(
        platform="test",
        description="简介",
        extra_lines=["附加"],
        ordered_contents=[
            OrderedContent("text", "正文"),
            OrderedContent("image", "https://img.example/a.jpg"),
            OrderedContent("image_error", "图片失败"),
        ],
    )

    assert result.content_lines == ["简介", "附加", "正文", "图片失败"]
    assert result.image_references == ["https://img.example/a.jpg"]
