from astrbot_multi_parser.core.contracts import OrderedContent
from astrbot_multi_parser.services.conversation import history as conversation_history
from result_factory import build_result


async def test_local_image_history_does_not_use_media_resolver(tmp_path, monkeypatch):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"image-bytes")

    class FailingResolver:
        def __init__(self, *args, **kwargs):
            raise AssertionError("local history images must bypass MediaResolver")

    monkeypatch.setattr(conversation_history, "MediaResolver", FailingResolver)
    result = build_result(
        platform="test",
        ordered_contents=[OrderedContent("image", str(image_path))],
    )

    content = await conversation_history.build_user_history_content("分享链接", result)

    assert isinstance(content, list)
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_cover_and_gallery_share_one_image(tmp_path):
    path = tmp_path / "cover.png"
    path.write_bytes(b"image")
    duplicate = tmp_path / "body.png"
    duplicate.write_bytes(path.read_bytes())
    result = build_result(
        platform="test",
        cover_urls=[str(path)],
        image_urls=[str(duplicate)],
        image_source_urls={
            str(path): "https://img.example/a",
            str(duplicate): "https://img.example/a",
        },
    )
    parts = await conversation_history.build_user_history_content("link", result)
    assert [part["type"] for part in parts] == ["text", "image_url", "text"]
    assert parts[2]["text"] == "[第 2 张图同第 1 张]"
