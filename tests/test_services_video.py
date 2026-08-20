import httpx
import pytest
from astrbot_multi_parser.services import video
from astrbot_multi_parser.services.video import (
    VideoSendPolicy,
    VideoSizeInfo,
    VideoSizeProbe,
)


def test_unknown_video_size_is_rejected_by_default():
    should_send, reason = VideoSendPolicy({}).decide(
        VideoSizeInfo(reason="服务端未返回视频大小")
    )

    assert should_send is False
    assert reason == "服务端未返回视频大小，未直接发送视频"


def test_video_size_policy_accepts_files_within_limit():
    should_send, reason = VideoSendPolicy({"max_video_size_mb": 1}).decide(
        VideoSizeInfo(size_bytes=512 * 1024)
    )

    assert should_send is True
    assert reason == "视频大小 0.50 MB，未超过限制"


@pytest.mark.asyncio
async def test_video_size_probe_uses_platform_proxy(monkeypatch):
    captured_options = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def head(self, url):
            request = httpx.Request("HEAD", url)
            return httpx.Response(
                200,
                headers={"Content-Length": "123"},
                request=request,
            )

    def create_client(**options):
        captured_options.update(options)
        return FakeClient()

    monkeypatch.setattr(video.httpx, "AsyncClient", create_client)
    probe = VideoSizeProbe(
        {
            "proxy_url": "http://proxy.example.com:8080",
            "proxy_switches": {"pixiv": True},
        },
        platform_name="pixiv",
    )

    size_info = await probe.probe("https://example.com/video.mp4")

    assert size_info.size_bytes == 123
    assert captured_options["proxy"] == "http://proxy.example.com:8080"
    assert captured_options["trust_env"] is False
