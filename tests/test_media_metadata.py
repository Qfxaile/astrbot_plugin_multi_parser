from pathlib import Path

from astrbot_multi_parser.core.contracts import MediaBundle, ParseResult


def test_parse_result_exposes_media_bundle():
    path = Path("/tmp/image.jpg")
    result = ParseResult(
        platform="test",
        temporary_files=[path],
        image_source_urls={str(path): "https://img.example/image.jpg"},
        video_download_host_suffixes=("example.com",),
    )

    media = result.media
    assert isinstance(media, MediaBundle)
    assert media.temporary_files == [path]
    assert media.image_source_urls[str(path)].startswith("https://")
    assert media.video_download_host_suffixes == ("example.com",)
