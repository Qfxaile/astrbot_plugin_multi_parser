import pytest
from astrbot_multi_parser.core.contracts import OrderedContent, ParseDiagnostics
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


@pytest.mark.parametrize("ordered", [False, True])
def test_image_slots_skip_absent_images_but_preserve_failures_and_duplicates(ordered):
    image = "https://img.example/a.jpg"
    result = build_result(
        platform="test",
        cover_urls=[""],
        image_urls=[image, "", image],
        diagnostics=ParseDiagnostics(image_errors={2: "图片失败"}),
        ordered_contents=(
            [
                OrderedContent("image", ""),
                OrderedContent("image", image),
                OrderedContent("image_error", ""),
                OrderedContent("image", image),
            ]
            if ordered
            else []
        ),
    )

    assert result.image_slots == [image, "", image]
    assert result.image_count == 3
    assert result.ordered_image_references == [image, image]
