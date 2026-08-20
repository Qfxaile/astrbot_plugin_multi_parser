"""解析 GitHub 仓库主页并发送官方 OpenGraph 卡片。"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser


class _OpenGraphImageParser(HTMLParser):
    """从 HTML 元数据中提取首个 OpenGraph 图片地址。"""

    def __init__(self) -> None:
        super().__init__()
        self.image_url = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.image_url or tag.lower() != "meta":
            return
        attributes = {name.lower(): value for name, value in attrs if value is not None}
        if attributes.get("property", "").lower() == "og:image":
            self.image_url = attributes.get("content", "").strip()


class GitHubParser(BaseParser):
    """识别公开仓库主页，并下载 GitHub 官方仓库卡片。"""

    name = "github"
    display_name = "GitHub"
    image_host_suffixes = (
        "opengraph.githubassets.com",
        "repository-images.githubusercontent.com",
    )
    repository_page_hosts = frozenset({"github.com", "www.github.com"})
    MAX_REPOSITORY_REDIRECTS = 5
    MAX_REPOSITORY_HTML_BYTES = 2 * 1024 * 1024
    URL_PATTERN = re.compile(
        r"https?://(?:www\.)?github\.com/[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
    REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}")
    HEADERS = {
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    async def match(self, context: ParseContext) -> bool:
        return self._find_repository(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        repository = self._find_repository(context.combined_text)
        if repository is None:
            return ParseResult(platform=self.name, error="未找到 GitHub 仓库链接。")

        owner, repo = repository
        repository_url = f"https://github.com/{owner}/{repo}"
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
                **self.http_client_options,
            ) as client:
                card_url = await self._fetch_opengraph_url(client, repository_url)
                result = ParseResult(
                    platform=self.name,
                    image_urls=[card_url],
                    image_download_headers={
                        "Referer": repository_url,
                        "User-Agent": self.HEADERS["User-Agent"],
                    },
                )
                return await self.materialize_images(result, client, repository_url)
        except httpx.HTTPError:
            return ParseResult(
                platform=self.name,
                error="GitHub仓库卡片请求失败，请稍后重试。",
            )

    async def _fetch_opengraph_url(
        self,
        client: httpx.AsyncClient,
        repository_url: str,
    ) -> str:
        """读取仓库页声明的官方卡片地址，避免随机缓存键触发限流。"""
        current_url = repository_url
        for redirect_count in range(self.MAX_REPOSITORY_REDIRECTS + 1):
            self._validate_repository_page_url(current_url)
            async with client.stream(
                "GET",
                current_url,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "*/*;q=0.8"
                    )
                },
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if redirect_count >= self.MAX_REPOSITORY_REDIRECTS or not location:
                        raise httpx.InvalidURL("too many repository redirects")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type.lower():
                    raise httpx.InvalidURL("repository page is not HTML")
                html = await self._read_repository_html(response)

            parser = _OpenGraphImageParser()
            parser.feed(html.decode("utf-8", errors="replace"))
            if not parser.image_url:
                raise httpx.InvalidURL("missing OpenGraph image URL")
            if not is_trusted_https_url(
                parser.image_url,
                self.image_host_suffixes,
                allow_fragment=False,
            ):
                raise httpx.InvalidURL("untrusted OpenGraph image URL")
            return parser.image_url

        raise httpx.InvalidURL("too many repository redirects")

    async def _read_repository_html(self, response: httpx.Response) -> bytes:
        """在固定大小上限内读取仓库页面，避免异常响应耗尽内存。"""
        content_length = response.headers.get("Content-Length", "")
        if (
            content_length.isdigit()
            and int(content_length) > self.MAX_REPOSITORY_HTML_BYTES
        ):
            raise httpx.HTTPError("repository page too large")

        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > self.MAX_REPOSITORY_HTML_BYTES:
                raise httpx.HTTPError("repository page too large")
        return bytes(content)

    def _validate_repository_page_url(self, url: str) -> None:
        """仅允许请求 GitHub 仓库页面，阻止重定向离开可信站点。"""
        try:
            hostname = (urlsplit(url).hostname or "").lower()
        except ValueError:
            hostname = ""
        if hostname not in self.repository_page_hosts or not is_trusted_https_url(
            url,
            self.repository_page_hosts,
            allow_fragment=False,
        ):
            raise httpx.InvalidURL("untrusted repository page URL")

    @classmethod
    def _find_repository(cls, text: str) -> tuple[str, str] | None:
        """返回消息中的首个仓库根链接，忽略所有仓库子路径。"""
        for match in cls.URL_PATTERN.finditer(text):
            candidate = match.group(0).rstrip(".,!?;:")
            if repository := cls._parse_repository_url(candidate):
                return repository
        return None

    @classmethod
    def _parse_repository_url(cls, url: str) -> tuple[str, str] | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 80, 443}
        ):
            return None

        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) != 2:
            return None
        owner, repo = path_parts
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if (
            cls.OWNER_PATTERN.fullmatch(owner) is None
            or cls.REPOSITORY_PATTERN.fullmatch(repo) is None
            or repo in {".", ".."}
        ):
            return None
        return owner, repo
