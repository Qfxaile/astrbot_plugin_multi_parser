import httpx
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.platforms.pixiv import PixivParser


async def test_pixiv_match_supports_artwork_and_legacy_links():
    parser = PixivParser({})

    assert await parser.match(
        ParseContext(text="https://www.pixiv.net/artworks/123456")
    )
    assert await parser.match(
        ParseContext(
            text="https://www.pixiv.net/member_illust.php?mode=medium&illust_id=123456"
        )
    )
    assert not await parser.match(
        ParseContext(text="https://www.pixiv.net/users/123456")
    )


def test_pixiv_parse_illust_payload_extracts_metadata_and_pages():
    parser = PixivParser({})
    result = parser._parse_illust_payload(
        {
            "illustType": 0,
            "title": "测试作品",
            "userName": "测试作者",
            "description": "简介<br>第二行",
            "tags": {"tags": [{"tag": "原创"}, {"tag": "风景"}]},
            "urls": {"original": "https://i.pximg.net/img-original/first.jpg"},
        },
        [
            {"urls": {"original": "https://i.pximg.net/img-original/first.jpg"}},
            {"urls": {"original": "https://i.pximg.net/img-original/second.jpg"}},
        ],
    )

    assert result.title == "测试作品"
    assert result.author == "测试作者"
    assert result.description == "简介 第二行"
    assert result.image_urls == [
        "https://i.pximg.net/img-original/first.jpg",
        "https://i.pximg.net/img-original/second.jpg",
    ]
    assert result.extra_lines == ["标签：原创、风景"]
    assert result.image_download_headers == {
        "Referer": "https://www.pixiv.net/",
        "User-Agent": parser.HEADERS["User-Agent"],
    }


def test_pixiv_parse_illust_payload_rejects_unsupported_types_and_unsafe_images():
    parser = PixivParser({})

    try:
        parser._parse_illust_payload({"illustType": 2}, [])
    except ValueError as exc:
        assert str(exc) == "Pixiv 动图暂不支持解析。"
    else:
        raise AssertionError("expected unsupported type error")

    try:
        parser._parse_illust_payload(
            {"illustType": 0, "urls": {"original": "http://evil.test/a.jpg"}},
            [],
        )
    except ValueError as exc:
        assert str(exc) == "Pixiv作品中未找到可发送的公开图片。"
    else:
        raise AssertionError("expected unsafe image error")


async def test_pixiv_parse_handles_public_ajax_responses(monkeypatch):
    responses = {
        "https://www.pixiv.net/ajax/illust/123456": {
            "error": False,
            "body": {
                "illustType": 0,
                "title": "测试作品",
                "userName": "测试作者",
                "description": "公开作品",
                "tags": {"tags": []},
            },
        },
        "https://www.pixiv.net/ajax/illust/123456/pages": {
            "error": False,
            "body": [
                {"urls": {"original": "https://i.pximg.net/img-original/first.jpg"}}
            ],
        },
    }

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse(responses[url])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: FakeClient())
    parser = PixivParser({})

    async def materialize_images(result, client, referer):
        return result

    monkeypatch.setattr(parser, "materialize_images", materialize_images)

    result = await parser.parse(
        ParseContext(text="https://www.pixiv.net/artworks/123456")
    )

    assert result.title == "测试作品"
    assert result.image_urls == ["https://i.pximg.net/img-original/first.jpg"]


async def test_pixiv_parse_returns_error_for_unavailable_ajax_payload(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"error": True, "message": "private detail"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: FakeClient())

    result = await PixivParser({}).parse(
        ParseContext(text="https://www.pixiv.net/artworks/123456")
    )

    assert result.error == "Pixiv作品不可访问，可能已删除或受到访问限制。"
    assert "private detail" not in result.error
