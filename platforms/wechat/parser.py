"""识别微信链接并分派到文章或视频号内容解析器。"""

import re
from collections.abc import Iterator

from ...core.contracts import ParseContext, ParseResult
from ...core.parser import BaseParser
from .article import WeChatArticleContent
from .channels import WeChatChannelsContent


class WeChatParser(WeChatArticleContent, WeChatChannelsContent, BaseParser):
    """解析微信公众号文章和微信视频号作品。"""

    name = "wechat"
    display_name = "微信"
    cookie_config_key = "wechat_yuanbao_cookies"
    image_host_suffixes = ("qpic.cn", "qlogo.cn", "finder.video.qq.com")
    ARTICLE_PATTERN = (
        r"https?://mp\.weixin\.qq\.com/s(?:"
        r"/[A-Za-z0-9_-]+(?:\?[^\s<>\"']*)?"
        r"|\?(?=[^\s<>\"']*__biz=)[^\s<>\"']+"
        r")"
    )
    CHANNELS_SHORT_PATTERN = (
        r"https?://weixin\.qq\.com/sph/[A-Za-z0-9_-]+"
        r"(?:\?[^\s<>\"']*)?"
    )
    CHANNELS_PREVIEW_PATTERN = (
        r"https?://channels\.weixin\.qq\.com/finder-preview/pages/"
        r"(?:sph|feed)\?[^\s<>\"']+"
    )
    ROUTES = (
        ("article", ARTICLE_PATTERN),
        ("channels", CHANNELS_SHORT_PATTERN),
        ("channels", CHANNELS_PREVIEW_PATTERN),
    )

    async def match(self, context: ParseContext) -> bool:
        """判断消息中是否包含受支持的微信内容链接。"""
        return any(
            self.parse_feature_enabled(kind) for kind, _ in self._routes(context)
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        """按链接类型解析公众号文章或视频号作品。"""
        matched = False
        for kind, url in self._routes(context):
            matched = True
            if not self.parse_feature_enabled(kind):
                continue
            if kind == "article":
                return await self._parse_article(url)
            return await self._parse_channels(url)
        if matched:
            return ParseResult.skipped(self.name)
        result = ParseResult(platform=self.name)
        result.diagnostics.error = "未找到受支持的微信链接。"
        return result

    def _routes(self, context: ParseContext) -> Iterator[tuple[str, str]]:
        text = context.combined_text
        # 保留公众号、视频号短链、视频号预览的原有优先级。
        for kind, pattern in self.ROUTES:
            if match := re.search(pattern, text):
                yield kind, self._clean_url(match.group(0))

    @staticmethod
    def _clean_url(url: str) -> str:
        return url.rstrip(".,;，。；、)）]】")
