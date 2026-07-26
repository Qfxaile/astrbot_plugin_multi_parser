"""识别微信链接并分派到文章或视频号内容解析器。"""

import re

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

    async def match(self, context: ParseContext) -> bool:
        """判断消息中是否包含受支持的微信内容链接。"""
        text = context.combined_text
        return any(
            re.search(pattern, text)
            for pattern in (
                self.ARTICLE_PATTERN,
                self.CHANNELS_SHORT_PATTERN,
                self.CHANNELS_PREVIEW_PATTERN,
            )
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        """按链接类型解析公众号文章或视频号作品。"""
        text = context.combined_text
        if match := re.search(self.ARTICLE_PATTERN, text):
            return await self._parse_article(self._clean_url(match.group(0)))
        for pattern in (self.CHANNELS_SHORT_PATTERN, self.CHANNELS_PREVIEW_PATTERN):
            if match := re.search(pattern, text):
                return await self._parse_channels(self._clean_url(match.group(0)))
        return ParseResult(platform=self.name, error="未找到受支持的微信链接。")

    @staticmethod
    def _clean_url(url: str) -> str:
        return url.rstrip(".,;，。；、)）]】")
