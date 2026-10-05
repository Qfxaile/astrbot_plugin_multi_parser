from astrbot_multi_parser.core.contracts import OrderedContent, ParseResult
from astrbot_multi_parser.services import conversation_history


async def test_local_image_history_does_not_use_media_resolver(tmp_path, monkeypatch):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"image-bytes")

    class FailingResolver:
        def __init__(self, *args, **kwargs):
            raise AssertionError("local history images must bypass MediaResolver")

    monkeypatch.setattr(conversation_history, "MediaResolver", FailingResolver)
    result = ParseResult(
        platform="test",
        ordered_contents=[OrderedContent("image", str(image_path))],
    )

    content = await conversation_history.build_parse_history_content(
        result, include_images=True
    )

    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
