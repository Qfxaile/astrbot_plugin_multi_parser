from pathlib import Path

from astrbot_multi_parser.core.contracts import MediaMetadata, ParseResult


def test_parse_result_exposes_media_metadata_view():
    path = Path("/tmp/image.jpg")
    result = ParseResult(
        platform="test",
        temporary_files=[path],
        image_source_urls={str(path): "https://img.example/image.jpg"},
        video_download_host_suffixes=("example.com",),
    )

    metadata = result.media_metadata
    assert isinstance(metadata, MediaMetadata)
    assert metadata.temporary_files == [path]
    assert metadata.image_source_urls[str(path)].startswith("https://")
    assert metadata.video_download_host_suffixes == ("example.com",)
