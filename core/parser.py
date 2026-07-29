"""定义平台解析器契约及跨平台共用流程。"""

from collections.abc import Mapping

import httpx

from .contracts import ParseContext, ParseResult
from .http import (
    AUTH_FAILURE_STATUS_CODES,
    CookieAccessError,
    build_cookie_access_error,
    cookie_config_value,
    raise_for_cookie_access,
    request_timeout,
)
from .media import ImageMaterializer


class BaseParser:
    """平台解析器的稳定契约。"""

    name = "base"
    # 子类通过声明元数据接入统一 Cookie 策略，不在平台模块重复状态判断。
    display_name = "平台"
    cookie_config_key = ""
    cookie_failure_status_codes = AUTH_FAILURE_STATUS_CODES
    image_host_suffixes: tuple[str, ...] = ()

    def __init__(self, config: Mapping[str, object]):
        self.config = config

    @property
    def request_timeout(self) -> float:
        return request_timeout(self.config)

    async def match(self, context: ParseContext) -> bool:
        raise NotImplementedError

    async def parse(self, context: ParseContext) -> ParseResult:
        raise NotImplementedError

    def cookie_access_error(self) -> CookieAccessError:
        """根据当前平台 Cookie 配置生成不泄漏凭据的用户提示。"""
        return build_cookie_access_error(
            self.display_name,
            cookie_config_value(self.config, self.cookie_config_key),
        )

    def raise_for_response_status(self, response: httpx.Response) -> None:
        """统一处理平台内容请求的 Cookie 拒绝和其他 HTTP 错误。

        Cookie 状态识别属于跨平台协议，集中在基类避免适配器复制；平台只声明
        配置键和特殊状态码。媒体下载不经过此入口，因此防盗链失败不会误报。
        """
        raise_for_cookie_access(
            response,
            platform=self.display_name,
            cookie_value=cookie_config_value(self.config, self.cookie_config_key),
            status_codes=self.cookie_failure_status_codes,
        )
        response.raise_for_status()

    async def materialize_images(
        self,
        result: ParseResult,
        client: httpx.AsyncClient,
        referer: str,
    ) -> ParseResult:
        materializer = ImageMaterializer(self.config, self.image_host_suffixes)
        return await materializer.materialize(result, client, referer)

    async def materialize_public_images(
        self,
        result: ParseResult,
        referer: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ParseResult:
        """使用不含平台 Cookie 的独立客户端读取公开图片。"""
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=False,
            headers=headers,
        ) as client:
            return await self.materialize_images(result, client, referer)
