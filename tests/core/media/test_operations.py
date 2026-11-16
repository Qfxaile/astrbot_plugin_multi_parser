import asyncio

import httpx
import pytest
from astrbot_multi_parser.core.media import TemporaryFileRegistry
from astrbot_multi_parser.core.media.operations import VideoMaterializer
from result_factory import build_result


@pytest.mark.parametrize("during_close", [False, True])
async def test_cancelled_video_removes_file_and_propagates_cancellation(
    tmp_path, monkeypatch, during_close
):
    blocked = asyncio.Event()

    class BlockedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * VideoMaterializer.CHUNK_SIZE
            if not during_close:
                blocked.set()
                await asyncio.Event().wait()

        async def aclose(self):
            if during_close:
                blocked.set()
                await asyncio.Event().wait()

    def handler(request):
        return httpx.Response(200, stream=BlockedStream())

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: client_type(
            transport=httpx.MockTransport(handler), **options
        ),
    )
    materializer = VideoMaterializer({}, ("video.example",))
    video_path = tmp_path / "partial.mp4"
    monkeypatch.setattr(materializer, "_new_video_path", lambda *_: video_path)
    result = build_result(platform="test", video_url="https://video.example/video.mp4")
    task = asyncio.create_task(materializer.materialize(result))
    try:
        await asyncio.wait_for(blocked.wait(), 2)
        assert video_path.stat().st_size == VideoMaterializer.CHUNK_SIZE
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not video_path.exists()
        assert TemporaryFileRegistry.paths(result) == ()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        TemporaryFileRegistry.cleanup(result)
