import re
from urllib.parse import quote, urlsplit, urlunsplit


class RedBookGalleryContent:
    """规范化小红书图集笔记的原图地址。"""

    @classmethod
    def _select_original_image_url(
        cls,
        image: object,
        fallback_fields: tuple[str, ...],
    ) -> str:
        if not isinstance(image, dict):
            return ""
        for field_name in ("fileId", "traceId"):
            image_id = image.get(field_name)
            if isinstance(image_id, str) and image_id:
                path = f"/{quote(image_id.lstrip('/'), safe='/')}"
                return urlunsplit(("https", "sns-img-qc.xhscdn.com", path, "", ""))

        failure_candidate = ""
        for field_name in fallback_fields:
            candidate = image.get(field_name)
            if not isinstance(candidate, str) or not candidate:
                continue
            try:
                parsed = urlsplit(candidate)
                port = parsed.port
            except ValueError:
                failure_candidate = failure_candidate or candidate
                continue
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or port not in {None, 80, 443}
            ):
                failure_candidate = failure_candidate or cls.INVALID_IMAGE_URL
                continue
            return cls._strip_image_transform(candidate)
        return failure_candidate

    @staticmethod
    def _strip_image_transform(url: str) -> str:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return url
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return "unsafe-image-url"
        try:
            port = parsed.port
        except ValueError:
            return url
        if port not in {None, 80, 443}:
            return "unsafe-image-url"
        path = re.sub(r"![^/]*$", "", parsed.path)
        return urlunsplit(parsed._replace(path=path))
