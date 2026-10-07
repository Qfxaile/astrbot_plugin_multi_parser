from pathlib import Path

from astrbot_multi_parser.core.contracts import MediaBundle, ParseResult
from astrbot_multi_parser.core.media import TemporaryFileRegistry


def test_parse_result_exposes_media_bundle():
    path = Path("/tmp/image.jpg")
    result = ParseResult(
        platform="test",
        temporary_files=[path],
        image_source_urls={str(path): "https://img.example/image.jpg"},
        media=MediaBundle(
            video_download_host_suffixes=("example.com",),
        ),
    )

    media = result.media
    assert isinstance(media, MediaBundle)
    assert TemporaryFileRegistry.paths(result) == (path,)
    assert media.image_source_urls[str(path)].startswith("https://")
    assert media.video_download_host_suffixes == ("example.com",)
