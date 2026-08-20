import httpx

from ...core.contracts import ParseResult
from .common import original_image_url


class BilibiliBangumiContent:
    """解析 B站番剧、电影和纪录片等影视内容介绍。"""

    BANGUMI_API = "https://api.bilibili.com/pgc/view/web/season"
    BANGUMI_TYPE_NAMES = {
        1: "番剧",
        2: "电影",
        3: "纪录片",
        4: "国创",
        5: "电视剧",
        7: "综艺",
    }

    async def _parse_bangumi(self, kind: str, content_id: str) -> ParseResult:
        page_url = f"https://www.bilibili.com/bangumi/play/{kind}{content_id}"
        parameter_name = "ep_id" if kind == "ep" else "season_id"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(page_url),
            cookies=self._cookies(),
            **self.http_client_options,
        ) as client:
            response = await client.get(
                self.BANGUMI_API,
                params={parameter_name: content_id},
            )
            self.raise_for_response_status(response)
            payload = response.json()
            self._raise_for_api_cookie_error(payload)
            if payload.get("code") != 0:
                message = payload.get("message") or "未知错误"
                return ParseResult(
                    platform=self.name,
                    error=f"获取影视信息失败: {message}",
                )
            result = self._parse_bangumi_payload(
                payload,
                episode_id=content_id if kind == "ep" else "",
            )
            return await self.materialize_images(result, client, page_url)

    def _parse_bangumi_payload(
        self,
        payload: dict,
        *,
        episode_id: str = "",
    ) -> ParseResult:
        data = payload.get("result") or {}
        cover_url = original_image_url(str(data.get("cover") or ""))
        extra_lines = []

        type_name = str(data.get("type_name") or "").strip()
        if not type_name:
            type_name = self.BANGUMI_TYPE_NAMES.get(data.get("type"), "")
        if type_name:
            extra_lines.append(f"类型: {type_name}")
        if areas := self._bangumi_names(data.get("areas")):
            extra_lines.append(f"地区: {' / '.join(areas)}")
        if styles := self._bangumi_names(data.get("styles")):
            extra_lines.append(f"风格: {' / '.join(styles)}")

        rating = data.get("rating") or {}
        score = rating.get("score")
        if score is not None:
            rating_text = f"评分: {score}"
            if rating_count := rating.get("count"):
                rating_text += f"（{rating_count:,} 人）"
            extra_lines.append(rating_text)

        new_episode = data.get("new_ep") or {}
        if status := str(new_episode.get("desc") or "").strip():
            extra_lines.append(f"状态: {status}")

        current_episode = next(
            (
                episode
                for episode in data.get("episodes") or []
                if str(episode.get("id") or "") == episode_id
            ),
            None,
        )
        if current_episode:
            episode_title = str(current_episode.get("title") or "").strip()
            long_title = str(current_episode.get("long_title") or "").strip()
            current_title = " - ".join(
                part for part in (episode_title, long_title) if part
            )
            if current_title:
                extra_lines.append(f"当前分集: {current_title}")

        return ParseResult(
            platform=self.name,
            title=str(data.get("season_title") or data.get("title") or "未知标题"),
            description=str(data.get("evaluate") or ""),
            cover_urls=[cover_url] if cover_url else [],
            extra_lines=extra_lines,
        )

    @staticmethod
    def _bangumi_names(items: object) -> list[str]:
        if not isinstance(items, list):
            return []
        return [
            name
            for item in items
            if isinstance(item, dict) and (name := str(item.get("name") or "").strip())
        ]
