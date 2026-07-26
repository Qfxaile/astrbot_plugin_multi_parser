"""识别贴吧帖子链接并分派到帖子内容解析器。"""

import re

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import cookie_config_value, parse_cookie_header
from ...core.parser import BaseParser
from .thread import TiebaThreadContent


class TiebaParser(TiebaThreadContent, BaseParser):
    """解析百度贴吧普通帖子页面的楼主首帖。"""

    name = "tieba"
    display_name = "贴吧"
    cookie_config_key = "tieba_cookies"
    image_host_suffixes = ("baidu.com", "bdimg.com", "bdstatic.com", "bcebos.com")
    THREAD_PATTERN = re.compile(
        r"https?://(?:www\.)?tieba\.baidu\.com/p/(?P<thread_id>\d+)"
        r"(?![A-Za-z0-9])"
        r"(?:[/?#][^\s]*)?",
        re.IGNORECASE,
    )
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    SECURITY_MARKERS = (
        "百度安全验证",
        "bioc_options",
        "请输入验证码",
        "seccaptcha.baidu.com",
    )
    DELETED_MARKERS = ("该贴已被删除", "该帖已被删除", "帖子已被删除")
    UNAVAILABLE_MARKERS = (
        "本吧暂不开放",
        "您访问的贴子不存在",
        "您访问的帖子不存在",
        "该贴暂时无法访问",
        "该帖暂时无法访问",
    )

    async def match(self, context: ParseContext) -> bool:
        return self.THREAD_PATTERN.search(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        matched = self.THREAD_PATTERN.search(context.combined_text)
        if not matched:
            return ParseResult(platform=self.name, error="未找到贴吧帖子链接。")

        thread_id = matched.group("thread_id")
        page_url = f"https://tieba.baidu.com/p/{thread_id}"
        request_headers = {"Cookie": self._legacy_page_cookie_header()}
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=False,
            headers=self.HEADERS,
        ) as client:
            response = await client.get(
                page_url,
                params={"see_lz": "1", "pn": "1"},
                headers=request_headers,
            )
            if response.status_code in {401, 403}:
                return self._cookie_failure_result()
            if 300 <= response.status_code < 400:
                return self._cookie_failure_result()
            response.raise_for_status()
            result = self._parse_page(response.text, thread_id)
            if result.error:
                return result
            return await self.materialize_images(result, client, page_url)

    def _legacy_page_cookie_header(self) -> str:
        """强制贴吧返回包含首帖正文的旧版服务端页面。"""
        cookie_parts = []
        legacy_switch_added = False
        raw_cookie = cookie_config_value(self.config, "tieba_cookies")
        for name, value in parse_cookie_header(raw_cookie):
            if name == "TIEBA_NEW_PC":
                if legacy_switch_added:
                    continue
                value = "0"
                legacy_switch_added = True
            cookie_parts.append(f"{name}={value.strip()}")
        if not legacy_switch_added:
            cookie_parts.append("TIEBA_NEW_PC=0")
        return "; ".join(cookie_parts)

    def _cookie_failure_result(self) -> ParseResult:
        """生成不包含 Cookie 内容的贴吧访问失败结果。"""
        return ParseResult(platform=self.name, error=str(self.cookie_access_error()))
