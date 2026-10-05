"""QQ JSON 分享卡片的公开字段提取。"""

import json


class ShareCardExtractor:
    """只提取公开链接、标题、预览图和腾讯频道公开帖子标识。"""

    @staticmethod
    def extract(data: str) -> tuple[str, str, str, dict[str, str]]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return "", "", "", {}
        if not isinstance(payload, dict):
            return "", "", "", {}
        meta = payload.get("meta", {})
        if not isinstance(meta, dict):
            return "", "", "", {}
        detail = meta.get("detail_1", {})
        news = meta.get("news", {})
        miniapp = meta.get("miniapp", {})
        feed = meta.get("feed", {})
        detail = detail if isinstance(detail, dict) else {}
        news = news if isinstance(news, dict) else {}
        miniapp = miniapp if isinstance(miniapp, dict) else {}
        feed = feed if isinstance(feed, dict) else {}
        url = (
            detail.get("qqdocurl", "")
            or news.get("jumpUrl", "")
            or miniapp.get("pcJumpUrl", "")
            or miniapp.get("legacyUrl", "")
            or feed.get("jumpUrl", "")
        )
        title = (
            feed.get("title", "")
            or str(payload.get("prompt", "")).removeprefix("[分享帖子]").strip()
        )
        preview = (
            news.get("preview", "")
            or miniapp.get("preview", "")
            or feed.get("cover", "")
        )
        feed_id = ShareCardExtractor.feed_id(feed)
        return (
            str(url or ""),
            str(title or ""),
            str(preview or ""),
            ({"feed_id": feed_id} if feed_id else {}),
        )

    @staticmethod
    def feed_id(feed: dict) -> str:
        busi_data = feed.get("busiData", {})
        if isinstance(busi_data, str):
            try:
                busi_data = json.loads(busi_data)
            except json.JSONDecodeError:
                busi_data = {}
        if isinstance(busi_data, dict):
            share_data = busi_data.get("share_biz_data", {})
            if isinstance(share_data, dict) and share_data.get("feed_id"):
                return str(share_data["feed_id"])
        return str(feed.get("ark_reserved3") or "")
