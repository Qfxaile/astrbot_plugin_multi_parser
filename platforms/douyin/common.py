from urllib.parse import urlsplit

import httpx


class DouyinContentSupport:
    """提供抖音各内容格式共享的会话和图片能力。"""

    INVALID_IMAGE_URL = "unsafe-image-url"
    TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
    AUTH_PATH_MARKERS = ("/passport/", "/verify", "/security/")

    @classmethod
    def _select_image_url(cls, image: object) -> str:
        """选择首个安全且不含抖音水印转换标记的图片地址。"""
        if not isinstance(image, dict):
            return ""
        failure_candidate = ""
        for field_name in ("url_list", "download_url_list"):
            candidates = image.get(field_name)
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
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
                if "-water:" in parsed.path:
                    failure_candidate = failure_candidate or cls.INVALID_IMAGE_URL
                    continue
                return candidate
        return failure_candidate

    async def _ensure_ttwid(self, client: httpx.AsyncClient):
        if any(cookie.name == "ttwid" for cookie in client.cookies.jar):
            return
        response = await client.post(
            self.TTWID_REGISTER_URL,
            headers={
                "Content-Type": "application/json",
                "Referer": "https://www.iesdouyin.com/",
            },
            json={
                "region": "cn",
                "aid": 1768,
                "needFid": False,
                "service": "www.iesdouyin.com",
                "union": True,
                "fid": "",
            },
        )
        self.raise_for_response_status(response)
        body = response.json()
        if callback_url := body.get("redirect_url"):
            callback = await client.get(
                callback_url,
                headers={"Referer": "https://www.iesdouyin.com/"},
            )
            self.raise_for_response_status(callback)
        if not any(cookie.name == "ttwid" for cookie in client.cookies.jar):
            raise ValueError("抖音匿名 ttwid 注册失败")

    def _raise_for_auth_page(self, response: httpx.Response) -> None:
        """识别分享页被重定向到登录或安全验证页面的情况。"""
        path = response.url.path.lower()
        if any(marker in path for marker in self.AUTH_PATH_MARKERS):
            raise self.cookie_access_error()
