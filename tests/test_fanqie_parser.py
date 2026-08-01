import json

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.platforms.fanqie import FanqieParser


@pytest.mark.asyncio
async def test_fanqie_parser_matches_public_share_link():
    parser = FanqieParser({})

    assert await parser.match(
        ParseContext(text="https://changdunovel.com/t/DTzryTPI27s/")
    )
    assert not await parser.match(
        ParseContext(text="https://changdunovel.com/not-a-share-link")
    )


def test_fanqie_parser_extracts_embedded_book_metadata():
    payload = {
        "pageData": {
            "bookInfo": {
                "book_name": "测试小说",
                "author": "测试作者",
                "abstract": "这是小说简介。",
                "thumb_url": "https://p3-novel.byteimg.com/test-cover.jpg",
            }
        }
    }
    html = (
        '<html><script type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></html>"
    )

    metadata = FanqieParser._extract_metadata(
        html,
        "https://www.changdunovel.com/t/DTzryTPI27s/",
    )

    assert metadata.title == "测试小说"
    assert metadata.author == "测试作者"
    assert metadata.description == "这是小说简介。"
    assert metadata.cover_url == "https://p3-novel.byteimg.com/test-cover.jpg"


def test_fanqie_parser_extracts_book_id_from_share_redirect():
    url = (
        "https://changdunovel.com/ug/pages/book-share?share_type=11"
        "&book_id=7638459797838236696&source_channel=copy_link"
    )

    assert FanqieParser._extract_book_id(url) == "7638459797838236696"
    assert (
        FanqieParser._extract_book_id(
            "https://changdunovel.com/ug/pages/book-share?book_id=invalid"
        )
        == ""
    )
    assert (
        FanqieParser._extract_book_id("https://evil.example/page/7638459797838236696")
        == ""
    )


@pytest.mark.asyncio
async def test_fanqie_parser_resolves_book_id_without_requesting_dynamic_page():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={
                "Location": (
                    "https://changdunovel.com/ug/pages/book-share?"
                    "book_id=7638459797838236696&encrypt_did=sensitive"
                )
            },
            request=request,
        )

    parser = FanqieParser({})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        book_id = await parser._resolve_book_id(
            client,
            "https://changdunovel.com/t/DTzryTPI27s/",
        )

    assert book_id == "7638459797838236696"
    assert requested_urls == ["https://changdunovel.com/t/DTzryTPI27s/"]


def test_fanqie_parser_extracts_real_detail_page_state():
    payload = {
        "common": {"name": ""},
        "page": {
            "bookId": "7638459797838236696",
            "bookName": "下载APP，才发现青梅好感爆了",
            "author": "八音",
            "abstract": "林舟一直以为自己和青梅早就疏远了。",
            "thumbUri": "https://p6-novel-sign.byteimg.com/novel-pic/test.image",
        },
    }
    html = (
        "<html><script>window.__INITIAL_STATE__="
        + json.dumps(payload, ensure_ascii=False)
        + ";</script></html>"
    )

    metadata = FanqieParser._extract_metadata(
        html,
        "https://fanqienovel.com/page/7638459797838236696",
        expected_book_id="7638459797838236696",
    )

    assert metadata.title == "下载APP，才发现青梅好感爆了"
    assert metadata.author == "八音"
    assert metadata.description == "林舟一直以为自己和青梅早就疏远了。"
    assert metadata.cover_url == (
        "https://p6-novel-sign.byteimg.com/novel-pic/test.image"
    )


def test_fanqie_parser_uses_open_graph_metadata_as_fallback():
    html = """
    <html><head>
      <meta property="og:title" content="分享小说">
      <meta name="description" content="分享简介">
      <meta property="og:image" content="/cover.jpg">
    </head></html>
    """

    metadata = FanqieParser._extract_metadata(
        html,
        "https://changdunovel.com/t/DTzryTPI27s/",
    )

    assert metadata.title == "分享小说"
    assert metadata.description == "分享简介"
    assert metadata.cover_url == "https://changdunovel.com/cover.jpg"
