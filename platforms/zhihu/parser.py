from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from ...core.contracts import ParseContext, ParseResult
from ...core.parser import BaseParser
from .handlers import (
    parse_answer_payload,
    parse_article_payload,
    parse_pin_payload,
    parse_question_payload,
)
from .request import ZhihuRequest, ZhihuRequestError


class ZhihuParser(BaseParser):
    """解析知乎问题、回答、专栏文章和想法。"""

    name = "zhihu"
    display_name = "知乎"
    cookie_config_key = "zhihu_cookies"
    image_host_suffixes = ("zhimg.com",)
    ANSWER_PATTERN = (
        r"https?://(?:www\.)?zhihu\.com/question/(?P<question_id>\d+)/"
        r"answer/(?P<answer_id>\d+)"
    )
    QUESTION_PATTERN = (
        r"https?://(?:www\.)?zhihu\.com/question/(?P<question_only_id>\d+)"
    )
    ARTICLE_PATTERN = r"https?://zhuanlan\.zhihu\.com/p/(?P<article_id>\d+)"
    TARDIS_ARTICLE_PATTERN = (
        r"https?://(?:www\.)?zhihu\.com/tardis/zm/art/"
        r"(?P<tardis_article_id>\d+)"
    )
    PIN_PATTERN = r"https?://(?:www\.)?zhihu\.com/pin/(?P<pin_id>\d+)"
    SHARE_PATTERN = r"https?://link\.zhihu\.com/\?[^\s#]+"
    PATTERNS = (
        ANSWER_PATTERN,
        QUESTION_PATTERN,
        ARTICLE_PATTERN,
        TARDIS_ARTICLE_PATTERN,
        PIN_PATTERN,
        SHARE_PATTERN,
    )

    async def match(self, context: ParseContext) -> bool:
        return any(
            re.search(pattern, context.combined_text) for pattern in self.PATTERNS
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        url = self._extract_url(context.combined_text)
        if not url:
            return ParseResult(platform=self.name, error="未找到知乎链接。")
        requester = ZhihuRequest(self.config)
        async with requester.create_client() as client:
            if re.search(self.SHARE_PATTERN, url):
                url = await requester.expand_share(client, url)
                if not self._is_trusted_zhihu_url(url):
                    raise ValueError("知乎分享链接跳转到不可信域名")
            result = await self._parse_url(requester, client, url)
            return await self.materialize_images(result, client, url)

    @classmethod
    def _extract_url(cls, text: str) -> str:
        for pattern in cls.PATTERNS:
            if match := re.search(pattern, text):
                return match.group(0)
        return ""

    @staticmethod
    def _is_trusted_zhihu_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "zhihu.com" or host.endswith(".zhihu.com")

    async def _parse_url(
        self, requester: ZhihuRequest, client, url: str
    ) -> ParseResult:
        if match := re.search(self.ANSWER_PATTERN, url):
            question_id = match.group("question_id")
            answer_id = match.group("answer_id")
            page_url = (
                f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
            )
            payload = await self._api_or_entity(
                requester,
                client,
                f"https://www.zhihu.com/api/v4/answers/{answer_id}",
                page_url,
                "answers",
                answer_id,
                params={"include": "content"},
                required_fields=("content", "contentHtml", "content_html"),
            )
            return parse_answer_payload(payload)

        if match := re.search(self.QUESTION_PATTERN, url):
            question_id = match.group("question_only_id")
            page_url = f"https://www.zhihu.com/question/{question_id}"
            question = await self._api_or_entity(
                requester,
                client,
                f"https://www.zhihu.com/api/v4/questions/{question_id}",
                page_url,
                "questions",
                question_id,
            )
            first_answer = None
            try:
                answers_payload = await requester.get_json(
                    client,
                    f"https://www.zhihu.com/api/v4/questions/{question_id}/answers",
                    params={
                        "limit": 1,
                        "offset": 0,
                        "sort_by": "default",
                        "include": "data[*].content",
                    },
                )
                answers = answers_payload.get("data")
                if isinstance(answers, list):
                    first_answer = next(
                        (item for item in answers if isinstance(item, dict)), None
                    )
                if first_answer and not first_answer.get("content"):
                    state = await self._load_initial_state(requester, client, page_url)
                    first_answer = self._first_entity(state, "answers", question_id)
            except ZhihuRequestError:
                state = await self._load_initial_state(requester, client, page_url)
                first_answer = self._first_entity(state, "answers", question_id)
            return parse_question_payload(question, first_answer)

        article_match = re.search(self.ARTICLE_PATTERN, url) or re.search(
            self.TARDIS_ARTICLE_PATTERN, url
        )
        if article_match:
            article_id = article_match.groupdict().get(
                "article_id"
            ) or article_match.groupdict().get("tardis_article_id")
            payload = await self._api_or_entity(
                requester,
                client,
                f"https://www.zhihu.com/api/v4/articles/{article_id}",
                url,
                "articles",
                str(article_id),
            )
            return parse_article_payload(payload)

        if match := re.search(self.PIN_PATTERN, url):
            pin_id = match.group("pin_id")
            payload = await self._api_or_entity(
                requester,
                client,
                f"https://www.zhihu.com/api/v4/pins/{pin_id}",
                url,
                "pins",
                pin_id,
            )
            return parse_pin_payload(payload)
        raise ValueError("知乎分享链接未包含受支持的内容地址")

    async def _api_or_entity(
        self,
        requester: ZhihuRequest,
        client,
        api_url: str,
        page_url: str,
        entity_name: str,
        entity_id: str,
        *,
        params: dict | None = None,
        required_fields: tuple[str, ...] = (),
    ) -> dict:
        try:
            payload = await requester.get_json(client, api_url, params=params)
            if payload and (
                not required_fields
                or any(payload.get(field) for field in required_fields)
            ):
                return payload
        except ZhihuRequestError:
            pass
        state = await self._load_initial_state(requester, client, page_url)
        entity = self._entity(state, entity_name, entity_id)
        if not isinstance(entity, dict):
            raise ValueError(f"知乎{self._entity_label(entity_name)}数据为空")
        return entity

    async def _load_initial_state(
        self, requester: ZhihuRequest, client, page_url: str
    ) -> dict:
        html_text = await requester.get_page(client, page_url)
        for pattern in (
            r'<script[^>]+id=["\']js-initialData["\'][^>]*>(.*?)</script>',
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        ):
            if match := re.search(pattern, html_text, re.DOTALL | re.IGNORECASE):
                try:
                    payload = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return payload
        raise ValueError("知乎页面中未找到初始状态数据")

    @staticmethod
    def _entity(state: dict, entity_name: str, entity_id: str):
        initial_state = state.get("initialState")
        if not isinstance(initial_state, dict):
            initial_state = (
                state.get("props") if isinstance(state.get("props"), dict) else {}
            )
        entities = (
            initial_state.get("entities") if isinstance(initial_state, dict) else None
        )
        if not isinstance(entities, dict):
            page_props = (
                initial_state.get("pageProps")
                if isinstance(initial_state, dict)
                else None
            )
            entities = (
                page_props.get("entities") if isinstance(page_props, dict) else None
            )
        mapping = entities.get(entity_name) if isinstance(entities, dict) else None
        if not isinstance(mapping, dict):
            return None
        return (
            mapping.get(str(entity_id)) or mapping.get(int(entity_id))
            if str(entity_id).isdigit()
            else mapping.get(str(entity_id))
        )

    @classmethod
    def _first_entity(cls, state: dict, entity_name: str, question_id: str):
        initial_state = state.get("initialState")
        entities = (
            initial_state.get("entities") if isinstance(initial_state, dict) else None
        )
        mapping = entities.get(entity_name) if isinstance(entities, dict) else None
        if not isinstance(mapping, dict):
            return None
        for item in mapping.values():
            if not isinstance(item, dict):
                continue
            question = item.get("question")
            current_id = question.get("id") if isinstance(question, dict) else None
            if current_id is None or str(current_id) == question_id:
                return item
        return None

    @staticmethod
    def _entity_label(entity_name: str) -> str:
        return {
            "answers": "回答",
            "questions": "问题",
            "articles": "文章",
            "pins": "想法",
        }.get(entity_name, "内容")
