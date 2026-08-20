import html
import json
import re

import httpx

from ...core.contracts import ParseResult
from .common import (
    clean_text,
    image_dedup_key,
    normalize_image_url,
    normalize_media_url,
)


def canonical_game_web_url(appid: str, game_type: str) -> str:
    normalized_type = game_type.strip().lower() or "pc"
    return f"https://www.xiaoheihe.cn/app/topic/game/{normalized_type}/{appid}"


def parse_game_state(
    html_text: str,
    appid: str,
    game_type: str,
    intro: dict,
) -> ParseResult:
    game = extract_game_root(html_text, appid)
    return build_game_result(html_text, game, appid, game_type, intro)


def build_game_result(
    html_text: str,
    game: dict,
    appid: str,
    game_type: str,
    intro: dict,
) -> ParseResult:
    del appid
    image_urls = extract_game_images(game, html_text)
    video_urls = extract_game_videos(game, html_text, intro)
    playable_video_urls = [url for url in video_urls if not is_hls_url(url)]
    video_url = playable_video_urls[0] if playable_video_urls else ""
    extra_lines = [f"游戏平台: {game_type.upper()}"]
    extra_lines.extend(f"备用视频: {url}" for url in video_urls if is_hls_url(url))
    if not image_urls and not video_url:
        extra_lines.append("未找到可发送的媒体。")
    return ParseResult(
        platform="xiaoheihe",
        title=build_game_title(game),
        description=build_game_desc(html_text, game, intro),
        image_urls=image_urls,
        video_url=video_url,
        keep_video_in_forward=True,
        extra_lines=extra_lines,
    )


def extract_game_root(html_text: str, appid: str) -> dict:
    payload = extract_nuxt_data_payload(html_text)
    if not payload:
        raise ValueError("小黑盒游戏页未找到 __NUXT_DATA__")
    root = devalue_resolve_root(payload)
    game = find_best_game_dict(root, appid)
    if not game:
        raise ValueError("小黑盒游戏页未找到游戏详情数据")
    return game


def extract_nuxt_data_payload(html_text: str) -> list | None:
    matched = re.search(
        r'<script[^>]+id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>',
        html_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not matched:
        return None
    try:
        payload = json.loads(matched.group(1).strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def devalue_resolve_root(payload: list):
    total = len(payload)
    memo: dict[int, object] = {}
    resolving: set[int] = set()

    def resolve(value):
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value < total
        ):
            return resolve_index(value)
        if isinstance(value, list):
            if (
                len(value) == 2
                and isinstance(value[0], str)
                and value[0]
                in {
                    "ShallowReactive",
                    "Reactive",
                    "Ref",
                    "ShallowRef",
                    "Readonly",
                    "ShallowReadonly",
                }
            ):
                return resolve(value[1])
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    def resolve_index(index: int):
        if index in memo:
            return memo[index]
        if index in resolving:
            return None
        resolving.add(index)
        memo[index] = None
        memo[index] = resolve(payload[index])
        resolving.remove(index)
        return memo[index]

    return resolve_index(0) if payload else None


def find_best_game_dict(root, appid: str) -> dict | None:
    best = None
    best_score = -1
    stack = [root]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, (dict, list)):
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
        if isinstance(current, dict):
            score = sum(
                3
                for key in (
                    "about_the_game",
                    "name",
                    "name_en",
                    "price",
                    "heybox_price",
                    "score",
                    "comment_stats",
                    "screenshots",
                    "share_url",
                    "video_url",
                )
                if key in current
            )
            if (
                str(current.get("appid") or "") == appid
                or str(current.get("steam_appid") or "") == appid
            ):
                score += 50
            if appid and appid in str(current.get("share_url") or ""):
                score += 20
            if score >= 12 and score > best_score:
                best = current
                best_score = score
            stack.extend(
                value for value in current.values() if isinstance(value, (dict, list))
            )
        elif isinstance(current, list):
            stack.extend(value for value in current if isinstance(value, (dict, list)))
    return best


def pick_steam_appid(game: dict, fallback_appid: str) -> int | None:
    try:
        return int(str(game.get("steam_appid") or fallback_appid).strip())
    except (TypeError, ValueError):
        return None


def build_game_title(game: dict) -> str:
    name = str(game.get("name") or "").strip()
    english_name = str(game.get("name_en") or "").strip()
    if name and english_name:
        return f"{name}（{english_name}）"
    return name or english_name or "小黑盒游戏详情"


def build_game_desc(html_text: str, game: dict, intro: dict) -> str:
    lines = []
    intro_text = format_game_intro_text(
        str(intro.get("about_the_game") or game.get("about_the_game") or "")
    )
    if intro_text:
        lines.append(intro_text)
    if game_types := parse_game_types_from_api(game) or parse_game_types_from_html(
        html_text
    ):
        lines.append(f"类型：{game_types}")
    score = str(game.get("score") or "").strip()
    stats = game.get("comment_stats")
    score_count = stats.get("score_comment") if isinstance(stats, dict) else None
    if score:
        if isinstance(score_count, int) and score_count > 0:
            lines.append(f"小黑盒评分：{score}（{format_people_count(score_count)}）")
        else:
            lines.append(f"小黑盒评分：{score}")
    release_date = str(intro.get("release_date") or "").strip()
    if release_date:
        lines.append(f"发布时间：{release_date.replace('-', '.')}")
    if developer := extract_company_text(intro.get("developers")):
        lines.append(f"开发商：{developer}")
    if publisher := extract_company_text(intro.get("publishers")):
        lines.append(f"发行商：{publisher}")
    price = game.get("price")
    if isinstance(price, dict):
        initial = str(price.get("initial") or price.get("current") or "").strip()
        if initial:
            lines.append(f"价格：¥ {initial.replace('¥', '').strip()}")
        lowest = str(price.get("lowest_price") or "").strip()
        if lowest:
            lines.append(f"史低价格：¥ {lowest.replace('¥', '').strip()}")
    heybox_price = game.get("heybox_price")
    if isinstance(heybox_price, dict):
        if yuan := format_yuan_from_coin(heybox_price.get("cost_coin")):
            lines.append(f"当前价格：¥ {yuan}")
    return "\n\n".join(lines)


def parse_game_types_from_api(game: dict) -> str:
    tags = game.get("common_tags")
    if not isinstance(tags, list):
        return ""
    common = []
    categories = []
    for item in tags:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "steam_aggre" and isinstance(
            item.get("desc_list"), list
        ):
            common.extend(
                clean_text(str(value)) for value in item["desc_list"] if value
            )
        elif item.get("type") == "simple_tag" and item.get("desc"):
            categories.append(clean_text(str(item["desc"])))
    groups = []
    if common:
        groups.append(f"[ {' '.join(common)} ]")
    if categories:
        groups.append(f"[ {' '.join(categories)} ]")
    return " ".join(groups)


def parse_game_types_from_html(html_text: str) -> str:
    matched = re.search(
        r'<div class="row-2">.*?<div class="tags">(.*?)</div></div>',
        html_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not matched:
        return ""
    tags_html = matched.group(1)
    common = re.search(
        r'<div class="tag common"[^>]*>(.*?)</div>',
        tags_html,
        re.DOTALL | re.IGNORECASE,
    )
    groups = []
    if common:
        values = [
            strip_tags(value)
            for value in re.findall(
                r"<span[^>]*>(.*?)</span>",
                common.group(1),
                re.DOTALL | re.IGNORECASE,
            )
        ]
        values = [value for value in values if value]
        if values:
            groups.append(f"[ {' '.join(values)} ]")
    values = [
        strip_tags(value)
        for value in re.findall(
            r'<p class="tag"[^>]*>(.*?)</p>',
            tags_html,
            re.DOTALL | re.IGNORECASE,
        )
    ]
    values = [value for value in values if value]
    if values:
        groups.append(f"[ {' '.join(values)} ]")
    return " ".join(groups)


def format_game_intro_text(text: str) -> str:
    if not text:
        return ""
    value = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return clean_text(value)


def strip_tags(text: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", "", html.unescape(text)))


def extract_company_text(items) -> str:
    if not isinstance(items, list):
        return ""
    return ",".join(
        str(item["value"])
        for item in items
        if isinstance(item, dict) and item.get("value")
    )


def format_people_count(count: int) -> str:
    if count >= 10000:
        return f"{count / 10000:.1f} 万人评价"
    return f"{count} 人评价"


def format_yuan_from_coin(coin) -> str:
    try:
        value = int(coin) / 1000
    except (TypeError, ValueError):
        return ""
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}"


def extract_game_images(game: dict, html_text: str) -> list[str]:
    images = []
    seen = set()

    def add(candidate):
        image_url = normalize_image_url(candidate)
        if not image_url:
            return
        image_key = image_dedup_key(image_url)
        if image_key in seen:
            return
        lowered = image_url.lower()
        if not any(
            marker in lowered
            for marker in ("gameimg", "steam_item_assets", "screenshot")
        ):
            return
        seen.add(image_key)
        images.append(image_url)

    for key in (
        "screenshots",
        "screenshot_list",
        "screen_shots",
        "images",
        "image_list",
        "game_imgs",
    ):
        values = game.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                for field in ("url", "thumbnail", "image", "img", "src"):
                    add(item.get(field))
            else:
                add(item)
    for field in (
        "header_img",
        "image",
        "cover",
        "cover_img",
        "poster",
        "share_img",
    ):
        add(game.get(field))
    if not images:
        for candidate in re.findall(
            r'https?://[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?',
            html_text,
            re.IGNORECASE,
        ):
            add(candidate)
    return images


def extract_game_videos(
    game: dict,
    html_text: str,
    intro: dict | None = None,
) -> list[str]:
    videos = []
    seen = set()

    def add(candidate):
        video_url = normalize_media_url(candidate)
        if video_url and video_url not in seen:
            seen.add(video_url)
            videos.append(video_url)

    if isinstance(intro, dict):
        for candidate in re.findall(
            r'https?://[^"\'\s<>]+\.mp4(?:\?[^"\'\s<>]*)?',
            str(intro.get("about_the_game") or ""),
            re.IGNORECASE,
        ):
            add(candidate)
    add(game.get("video_url"))
    screenshots = game.get("screenshots")
    if isinstance(screenshots, list):
        for item in screenshots:
            if not isinstance(item, dict) or item.get("type") != "movie":
                continue
            add(item.get("url") or item.get("video_url"))
    for candidate in re.findall(
        r'https?://[^"\'\s<>]+\.(?:m3u8|mp4|mov)(?:\?[^"\'\s<>]*)?',
        html_text,
        re.IGNORECASE,
    ):
        add(candidate)
    return videos


def is_hls_url(url: str) -> bool:
    return bool(re.search(r"\.m3u8(?:$|[?#])", url, re.IGNORECASE))


class XiaoheiheGameContent:
    """请求并解析小黑盒游戏详情。"""

    async def _parse_game_by_appid(self, appid: str, game_type: str) -> ParseResult:
        appid = appid.strip()
        if not appid:
            raise ValueError("无效的小黑盒游戏 appid")
        cookie_headers = self._request_cookie_headers()
        web_url = canonical_game_web_url(appid, game_type)
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=self.HEADERS,
            **self.http_client_options,
        ) as client:
            detail_response = await client.get(
                "https://api.xiaoheihe.cn/game/get_game_detail/",
                params={
                    "app": "heybox",
                    "os_type": "web",
                    "x_app": "heybox_website",
                    "x_client_type": "web",
                    "x_os_type": "Windows",
                    "x_client_version": "",
                    "client_type": "web",
                    "web_version": "3.0",
                    "version": "999.0.4",
                    "steam_appid": appid,
                    **self._sign_path("/game/get_game_detail/"),
                },
                headers=cookie_headers,
            )
            self.raise_for_response_status(detail_response)
            detail_payload = detail_response.json()
            if (
                not isinstance(detail_payload, dict)
                or detail_payload.get("status") != "ok"
                or not isinstance(detail_payload.get("result"), dict)
            ):
                self._raise_for_payload_error(detail_payload)
                raise ValueError("小黑盒 get_game_detail 请求失败")
            game = detail_payload["result"]
            steam_appid = pick_steam_appid(game, appid)
            intro: dict = {}
            if steam_appid is not None:
                intro_response = await client.get(
                    "https://api.xiaoheihe.cn/game/game_introduction/",
                    params={"steam_appid": steam_appid, "return_json": 1},
                    headers=cookie_headers,
                )
                intro_response.raise_for_status()
                intro_payload = intro_response.json()
                if (
                    isinstance(intro_payload, dict)
                    and intro_payload.get("status") == "ok"
                    and isinstance(intro_payload.get("result"), dict)
                ):
                    intro = intro_payload["result"]
            result = build_game_result("", game, appid, game_type, intro)
            return await self.materialize_images(result, client, web_url)
