from astrbot_multi_parser.services.share_card import ShareCardExtractor


def test_share_card_extracts_public_feed_id_without_tokens():
    url, title, preview, metadata = ShareCardExtractor.extract(
        '{"prompt":"[分享帖子]标题","meta":{"feed":{"jumpUrl":"https://example.com/post","cover":"https://img.example/a.jpg","busiData":{"share_biz_data":{"feed_id":"feed-1","token":"secret"}}}}}'
    )

    assert (url, title, preview) == (
        "https://example.com/post",
        "标题",
        "https://img.example/a.jpg",
    )
    assert metadata == {"feed_id": "feed-1"}
