import asyncio
from pathlib import Path

import httpx
import pytest
from astrbot.api.message_components import Image, Plain, Record
from astrbot_multi_parser import core as models


def test_core_exports_contracts():
    from astrbot_multi_parser.core.contracts import ParseResult as CoreParseResult

    assert models.ParseResult is CoreParseResult


def test_invalid_legacy_image_slots_are_marked_in_original_order():
    from astrbot_multi_parser.core.media import mark_invalid_legacy_images

    result = models.ParseResult(
        platform="test",
        cover_urls=["unsafe-image-url"],
        image_urls=["https://safe.test/1.jpg", "unsafe-image-url"],
    )

    mark_invalid_legacy_images(result, "unsafe-image-url")

    assert result.cover_urls == [""]
    assert result.image_urls == ["https://safe.test/1.jpg", ""]
    assert result.image_errors == {
        0: "第 1 张图片获取失败：InvalidURL",
        2: "第 3 张图片获取失败：InvalidURL",
    }


@pytest.mark.asyncio
async def test_materialize_images_respects_download_concurrency(monkeypatch, tmp_path):
    from astrbot_multi_parser.core.media import ImageMaterializer

    active_downloads = 0
    peak_downloads = 0

    async def fake_download(self, client, image_url, referer):
        nonlocal active_downloads, peak_downloads
        active_downloads += 1
        peak_downloads = max(peak_downloads, active_downloads)
        await asyncio.sleep(0.01)
        image_path = tmp_path / Path(image_url).name
        image_path.write_bytes(image_url.encode())
        active_downloads -= 1
        return image_path

    monkeypatch.setattr(ImageMaterializer, "_download_image", fake_download)
    image_urls = [f"https://img.example/{index}.jpg" for index in range(4)]
    result = models.ParseResult(platform="test", image_urls=image_urls.copy())

    async with httpx.AsyncClient() as client:
        await models.BaseParser({"image_download_concurrency": 2}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert peak_downloads == 2
    assert [Path(value).name for value in result.image_urls] == [
        "0.jpg",
        "1.jpg",
        "2.jpg",
        "3.jpg",
    ]
    result.cleanup_temporary_files()


@pytest.mark.asyncio
async def test_materialize_images_streams_original_bytes_to_temporary_file(tmp_path):
    class ChunkedImageStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b"original-", b"image-", b"bytes"):
                yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "image/webp"},
            stream=ChunkedImageStream(),
            request=request,
        )

    result = models.ParseResult(
        platform="douyin",
        image_urls=["https://img.example/original.webp"],
    )
    parser = models.BaseParser({"image_temp_dir": str(tmp_path)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await parser.materialize_images(result, client, "https://share.example/post/1")

    image_path = Path(result.image_urls[0])
    assert image_path.parent == tmp_path
    assert image_path.suffix == ".webp"
    assert image_path.read_bytes() == b"original-image-bytes"
    assert not result.image_urls[0].startswith("base64://")
    assert result.temporary_files == [image_path]
    assert result.image_source_urls == {
        str(image_path.resolve()): "https://img.example/original.webp"
    }

    component = result.info_chain(include_summary=False)[0]
    assert component.file == image_path.resolve().as_uri()
    assert component.path == str(image_path.resolve())

    result.cleanup_temporary_files()
    assert not image_path.exists()
    assert result.temporary_files == []
    assert result.image_source_urls == {}


@pytest.mark.asyncio
async def test_materialize_images_preserves_bytes_headers_and_temporary_slots(
    assert_temporary_image,
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"\x00raw-image\xff", request=request)

    result = models.ParseResult(
        platform="douyin",
        cover_urls=["base64://YWxyZWFkeQ=="],
        image_urls=["https://img.example/raw.webp"],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "session-agent"},
        cookies={"session": "cookie-value"},
    ) as client:
        returned = await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert returned is result
    assert result.cover_urls == ["base64://YWxyZWFkeQ=="]
    assert_temporary_image(result, result.image_urls[0], b"\x00raw-image\xff")
    assert len(requests) == 1
    assert requests[0].headers["Referer"] == "https://share.example/post/1"
    assert requests[0].headers["User-Agent"] == "session-agent"
    assert requests[0].headers["Cookie"] == "session=cookie-value"


@pytest.mark.asyncio
async def test_materialize_images_keeps_failed_legacy_slot_and_index(
    assert_temporary_image,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/failed.jpg":
            return httpx.Response(403, request=request)
        return httpx.Response(200, content=request.url.path.encode(), request=request)

    result = models.ParseResult(
        platform="douyin",
        cover_urls=["https://img.example/cover.jpg"],
        image_urls=[
            "https://img.example/failed.jpg",
            "https://img.example/final.jpg",
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert_temporary_image(result, result.cover_urls[0], b"/cover.jpg")
    assert result.image_urls[0] == ""
    assert_temporary_image(result, result.image_urls[1], b"/final.jpg")
    assert result.image_errors == {1: "第 2 张图片获取失败：HTTP 403"}


@pytest.mark.asyncio
async def test_materialize_images_preserves_ordered_text_and_marks_failure(
    assert_temporary_image,
):
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/failed.jpg":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, content=b"ordered-image", request=request)

    result = models.ParseResult(
        platform="bilibili",
        image_urls=["https://legacy.example/should-not-download.jpg"],
        ordered_contents=[
            models.OrderedContent(kind="text", value="第一段"),
            models.OrderedContent(kind="image", value="https://img.example/failed.jpg"),
            models.OrderedContent(kind="text", value="第二段"),
            models.OrderedContent(
                kind="image", value="https://img.example/working.jpg"
            ),
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert result.ordered_contents[:3] == [
        models.OrderedContent(kind="text", value="第一段"),
        models.OrderedContent(
            kind="image_error", value="第 1 张图片获取失败：ReadTimeout"
        ),
        models.OrderedContent(kind="text", value="第二段"),
    ]
    assert result.ordered_contents[3].kind == "image"
    assert_temporary_image(result, result.ordered_contents[3].value, b"ordered-image")
    assert result.image_urls == ["https://legacy.example/should-not-download.jpg"]
    assert requested_urls == [
        "https://img.example/failed.jpg",
        "https://img.example/working.jpg",
    ]


@pytest.mark.asyncio
async def test_materialize_images_propagates_non_http_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected decoder failure")

    result = models.ParseResult(
        platform="douyin",
        image_urls=["https://img.example/image.jpg"],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="unexpected decoder failure"):
            await models.BaseParser({}).materialize_images(
                result, client, "https://share.example/post/1"
            )


@pytest.mark.asyncio
async def test_materialize_images_logs_only_hostname_and_error_summary(caplog):
    image_url = "https://secret.example/private/token.jpg?signature=sensitive"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    result = models.ParseResult(platform="douyin", image_urls=[image_url])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    warning = next(
        record.message
        for record in caplog.records
        if record.message.startswith("图片下载失败")
    )
    assert "secret.example" in warning
    assert "HTTP 401" in warning
    assert image_url not in warning
    assert "/private/token.jpg" not in warning
    assert "signature=sensitive" not in warning


@pytest.mark.asyncio
async def test_materialize_images_skips_ordered_base64_image():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"unexpected", request=request)

    original_value = "base64://b3JkZXJlZC1pbWFnZQ=="
    result = models.ParseResult(
        platform="bilibili",
        ordered_contents=[models.OrderedContent(kind="image", value=original_value)],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert requested_urls == []
    assert result.ordered_contents == [
        models.OrderedContent(kind="image", value=original_value)
    ]


@pytest.mark.asyncio
async def test_materialize_images_converts_malformed_legacy_url_to_error(caplog):
    result = models.ParseResult(platform="douyin", image_urls=["http://[::1"])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: None)
    ) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert result.image_urls == [""]
    assert result.image_errors == {0: "第 1 张图片获取失败：InvalidURL"}
    assert any(
        record.message == "图片下载失败 (unknown): InvalidURL"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_materialize_images_converts_malformed_ordered_url_to_error(caplog):
    result = models.ParseResult(
        platform="bilibili",
        ordered_contents=[models.OrderedContent(kind="image", value="http://[::1")],
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: None)
    ) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert result.ordered_contents == [
        models.OrderedContent(
            kind="image_error", value="第 1 张图片获取失败：InvalidURL"
        )
    ]
    assert any(
        record.message == "图片下载失败 (unknown): InvalidURL"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_materialize_images_rejects_unsafe_legacy_urls_without_requests(
    assert_temporary_image,
):
    requested_urls = []
    unsafe_urls = [
        "ftp://img.example/file.jpg",
        "https://user:password@img.example/file.jpg",
        "https://localhost/file.jpg",
        "https://cdn.localhost/file.jpg",
        "https://cdn.local/file.jpg",
        "https://cdn.internal/file.jpg",
        "https://127.0.0.1/file.jpg",
        "https://[::1]/file.jpg",
        "https://img.example:99999/file.jpg",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"unexpected", request=request)

    result = models.ParseResult(
        platform="bilibili",
        image_urls=[*unsafe_urls, "https://img.example:443/ok.jpg"],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert requested_urls == ["https://img.example/ok.jpg"]
    assert result.image_urls[: len(unsafe_urls)] == ["" for _ in unsafe_urls]
    assert all(
        result.image_errors[index].startswith(f"第 {index + 1} 张图片获取失败：")
        for index in range(len(unsafe_urls))
    )
    assert_temporary_image(result, result.image_urls[-1], b"unexpected")


@pytest.mark.asyncio
async def test_materialize_images_rejects_unsafe_ordered_urls_and_allows_public_ipv6(
    assert_temporary_image,
):
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"public-image", request=request)

    result = models.ParseResult(
        platform="bilibili",
        ordered_contents=[
            models.OrderedContent(kind="image", value="https://10.0.0.1/private.jpg"),
            models.OrderedContent(
                kind="image", value="https://[2001:4860:4860::8888]:443/public.jpg"
            ),
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await models.BaseParser({}).materialize_images(
            result, client, "https://share.example/post/1"
        )

    assert requested_urls == ["https://[2001:4860:4860::8888]/public.jpg"]
    assert result.ordered_contents[0] == models.OrderedContent(
        kind="image_error", value="第 1 张图片获取失败：InvalidURL"
    )
    assert_temporary_image(result, result.ordered_contents[1].value, b"public-image")


@pytest.mark.asyncio
async def test_materialize_images_rejects_nonstandard_port_before_request():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"unexpected", request=request)

    parser = models.BaseParser({})
    parser.image_host_suffixes = ("trusted.example",)
    result = models.ParseResult(
        platform="test", image_urls=["https://cdn.trusted.example:8443/image.jpg"]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await parser.materialize_images(result, client, "https://share.example/post/1")

    assert requested_urls == []
    assert result.image_urls == [""]
    assert result.image_errors == {0: "第 1 张图片获取失败：InvalidURL"}


@pytest.mark.asyncio
async def test_materialize_images_rejects_redirect_to_private_host():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": "http://127.0.0.1/private.jpg"}, request=request
        )

    parser = models.BaseParser({})
    parser.image_host_suffixes = ("trusted.example",)
    result = models.ParseResult(
        platform="test", image_urls=["https://cdn.trusted.example/start.jpg"]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await parser.materialize_images(result, client, "https://share.example/post/1")

    assert requested_urls == ["https://cdn.trusted.example/start.jpg"]
    assert result.image_urls == [""]
    assert result.image_errors == {0: "第 1 张图片获取失败：InvalidURL"}


@pytest.mark.asyncio
async def test_materialize_images_follows_safe_relative_redirect(
    assert_temporary_image,
):
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/start.jpg":
            return httpx.Response(
                302, headers={"Location": "/final.jpg"}, request=request
            )
        return httpx.Response(200, content=b"redirected-image", request=request)

    parser = models.BaseParser({})
    parser.image_host_suffixes = ("trusted.example",)
    result = models.ParseResult(
        platform="test", image_urls=["https://cdn.trusted.example/start.jpg"]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await parser.materialize_images(result, client, "https://share.example/post/1")

    assert requested_urls == [
        "https://cdn.trusted.example/start.jpg",
        "https://cdn.trusted.example/final.jpg",
    ]
    assert_temporary_image(result, result.image_urls[0], b"redirected-image")


@pytest.mark.asyncio
async def test_materialize_images_stops_after_five_redirects():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        index = int(request.url.path.removeprefix("/hop-").removesuffix(".jpg"))
        return httpx.Response(
            302,
            headers={"Location": f"/hop-{index + 1}.jpg"},
            request=request,
        )

    parser = models.BaseParser({})
    parser.image_host_suffixes = ("trusted.example",)
    result = models.ParseResult(
        platform="test", image_urls=["https://cdn.trusted.example/hop-0.jpg"]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await parser.materialize_images(result, client, "https://share.example/post/1")

    assert requested_urls == [
        f"https://cdn.trusted.example/hop-{index}.jpg" for index in range(6)
    ]
    assert result.image_urls == [""]
    assert result.image_errors == {0: "第 1 张图片获取失败：InvalidURL"}


def test_info_chain_preserves_ordered_text_and_images():
    result = models.ParseResult(
        platform="bilibili",
        title="标题",
        author="作者",
        ordered_contents=[
            models.OrderedContent(kind="text", value="第一段"),
            models.OrderedContent(kind="image", value="https://img.example/1.jpg"),
            models.OrderedContent(kind="text", value="第二段"),
        ],
    )

    chain = result.info_chain()

    assert [type(item) for item in chain] == [Plain, Plain, Image, Plain]
    assert chain[0].text == "标题\n作者: 作者"
    assert chain[1].text == "第一段"
    assert chain[2].file == "https://img.example/1.jpg"
    assert chain[3].text == "第二段"


def test_info_chain_keeps_legacy_media_order_without_ordered_content():
    result = models.ParseResult(
        platform="douyin",
        title="标题",
        image_urls=["https://img.example/1.jpg"],
    )

    chain = result.info_chain()

    assert [type(item) for item in chain] == [Image, Plain]
    assert chain[0].file == "https://img.example/1.jpg"
    assert chain[1].text == "标题"


def test_info_chain_accepts_materialized_base64_image():
    result = models.ParseResult(
        platform="douyin",
        title="图文标题",
        image_urls=["base64://aW1hZ2U="],
    )

    chain = result.info_chain()

    assert isinstance(chain[0], Image)
    assert chain[0].file == "base64://aW1hZ2U="
    assert isinstance(chain[1], Plain)


def test_image_count_includes_successful_and_failed_image_slots():
    result = models.ParseResult(
        platform="bilibili",
        cover_urls=["https://img.example/cover.jpg", ""],
        image_urls=["https://img.example/1.jpg"],
        image_errors={1: "封面下载失败"},
        ordered_contents=[
            models.OrderedContent(kind="text", value="正文"),
            models.OrderedContent(kind="image", value="https://img.example/2.jpg"),
            models.OrderedContent(kind="image_error", value="图片下载失败"),
        ],
    )

    assert result.image_count == 5


def test_info_chain_can_render_summary_only():
    result = models.ParseResult(
        platform="bilibili",
        title="标题",
        author="作者",
        description="简介内容",
        cover_urls=["https://img.example/cover.jpg"],
        video_url="https://video.example/1.mp4",
        error="解析失败",
        extra_lines=["额外信息"],
        ordered_contents=[models.OrderedContent(kind="text", value="正文")],
    )

    chain = result.info_chain(
        include_video_url=True,
        include_summary=True,
        include_content=False,
    )

    assert [type(item) for item in chain] == [Plain]
    assert chain[0].text == (
        "标题\n作者: 作者\n简介:\n简介内容\n额外信息\n解析失败\n"
        "视频链接: https://video.example/1.mp4"
    )


def test_info_chain_can_render_ordered_content_only_with_image_error():
    result = models.ParseResult(
        platform="bilibili",
        title="标题",
        ordered_contents=[
            models.OrderedContent(kind="text", value="第一段"),
            models.OrderedContent(kind="image_error", value="图片下载失败"),
            models.OrderedContent(kind="image", value="https://img.example/1.jpg"),
        ],
    )

    chain = result.info_chain(include_summary=False, include_content=True)

    assert [type(item) for item in chain] == [Plain, Plain, Image]
    assert chain[0].text == "第一段"
    assert chain[1].text == "图片下载失败"
    assert chain[2].file == "https://img.example/1.jpg"


def test_info_chain_keeps_legacy_slots_and_errors_before_summary():
    result = models.ParseResult(
        platform="douyin",
        title="标题",
        cover_urls=["https://img.example/cover.jpg", ""],
        image_urls=["", "https://img.example/1.jpg"],
        image_errors={1: "封面下载失败", 2: "正文图片下载失败"},
    )

    chain = result.info_chain()

    assert [type(item) for item in chain] == [Image, Plain, Plain, Image, Plain]
    assert chain[0].file == "https://img.example/cover.jpg"
    assert chain[1].text == "封面下载失败"
    assert chain[2].text == "正文图片下载失败"
    assert chain[3].file == "https://img.example/1.jpg"
    assert chain[4].text == "标题"


def test_info_chain_can_render_legacy_content_only():
    result = models.ParseResult(
        platform="douyin",
        title="标题",
        image_urls=["https://img.example/1.jpg"],
    )

    chain = result.info_chain(include_summary=False, include_content=True)

    assert [type(item) for item in chain] == [Image]


def test_parse_result_preserves_legacy_positional_arguments():
    ordered_contents = [models.OrderedContent(kind="text", value="正文")]

    result = models.ParseResult(
        "bilibili",
        "标题",
        "作者",
        "简介",
        ["https://img.example/cover.jpg"],
        ["https://img.example/1.jpg"],
        "https://video.example/1.mp4",
        "解析失败",
        ["额外信息"],
        ordered_contents,
    )

    assert result.video_url == "https://video.example/1.mp4"
    assert result.error == "解析失败"
    assert result.extra_lines == ["额外信息"]
    assert result.ordered_contents is ordered_contents
    assert result.image_errors == {}


def test_audio_chain_builds_remote_record_component():
    result = models.ParseResult(
        platform="douyin",
        audio_url="https://v3-luna.douyinvod.com/song.m4a",
    )

    chain = result.audio_chain()

    assert len(chain) == 1
    assert isinstance(chain[0], Record)
    assert chain[0].file == result.audio_url


def test_info_chain_returns_empty_when_summary_and_content_are_disabled():
    result = models.ParseResult(
        platform="douyin",
        title="标题",
        image_urls=["https://img.example/1.jpg"],
    )

    assert result.info_chain(include_summary=False, include_content=False) == []


def test_info_chain_can_render_legacy_summary_only():
    result = models.ParseResult(
        platform="douyin",
        title="标题",
        image_urls=["https://img.example/1.jpg"],
    )

    chain = result.info_chain(include_summary=True, include_content=False)

    assert [type(item) for item in chain] == [Plain]
    assert chain[0].text == "标题"


def test_info_chain_skips_empty_ordered_content_values():
    result = models.ParseResult(
        platform="bilibili",
        ordered_contents=[
            models.OrderedContent(kind="text", value=""),
            models.OrderedContent(kind="image", value=""),
            models.OrderedContent(kind="image_error", value=""),
        ],
    )

    assert result.info_chain() == []
    assert result.image_count == 2


def test_info_chain_skips_legacy_empty_url_without_error_but_counts_slot():
    result = models.ParseResult(
        platform="douyin",
        title="标题",
        image_urls=[""],
    )

    chain = result.info_chain()

    assert [type(item) for item in chain] == [Plain]
    assert chain[0].text == "标题"
    assert result.image_count == 1
