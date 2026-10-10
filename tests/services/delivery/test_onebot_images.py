from astrbot_multi_parser.services.delivery.onebot_images import OneBotImageDownloader


def test_downloaded_file_path_accepts_nested_response():
    assert (
        OneBotImageDownloader.downloaded_file_path({"data": {"path": "/tmp/image.jpg"}})
        == "/tmp/image.jpg"
    )
