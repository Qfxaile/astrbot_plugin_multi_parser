class RedBookVideoContent:
    """选择小红书视频笔记的首选播放流。"""

    @staticmethod
    def _select_video_url(video: object) -> str:
        if not isinstance(video, dict):
            return ""
        media = video.get("media")
        if not isinstance(media, dict):
            return ""
        stream = media.get("stream")
        if not isinstance(stream, dict):
            return ""
        for codec in ("h265", "h264", "av1", "h266"):
            variants = stream.get(codec) or []
            if not isinstance(variants, list):
                continue
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                if isinstance(variant.get("masterUrl"), str) and variant["masterUrl"]:
                    return variant["masterUrl"]
        return ""
