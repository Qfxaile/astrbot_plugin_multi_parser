import asyncio
import ipaddress
import mimetypes
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from astrbot.api import logger

from .contracts import ParseResult
from .http import is_trusted_https_url, request_timeout

FORBIDDEN_MEDIA_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def sanitize_media_headers(
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    """保留安全媒体请求头，剔除凭据与非法换行。"""
    sanitized: dict[str, str] = {}
    for name, value in (headers or {}).items():
        name_text = str(name).strip()
        value_text = str(value)
        if (
            not name_text
            or name_text.lower() in FORBIDDEN_MEDIA_HEADERS
            or any(
                character in name_text or character in value_text
                for character in "\r\n"
            )
        ):
            continue
        sanitized[name_text] = value_text
    return sanitized


def mark_invalid_legacy_images(
    result: ParseResult,
    invalid_marker: str,
    *,
    error_detail: str = "InvalidURL",
) -> None:
    """将旧版图片列表中的无效候选转换为保持原索引的错误槽位。"""
    image_number = 0
    legacy_index = 0
    for field_name in ("cover_urls", "image_urls"):
        image_values = getattr(result, field_name)
        for field_index, image_url in enumerate(image_values):
            image_number += 1
            if image_url == invalid_marker:
                image_values[field_index] = ""
                result.image_errors[legacy_index] = (
                    f"第 {image_number} 张图片获取失败：{error_detail}"
                )
            legacy_index += 1


class ImageMaterializer:
    """安全地将解析结果中的远程图片流式写入临时文件。"""

    DEFAULT_DOWNLOAD_CONCURRENCY = 4
    MAX_DOWNLOAD_CONCURRENCY = 16

    def __init__(
        self,
        config: Mapping[str, object],
        allowed_host_suffixes: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.allowed_host_suffixes = allowed_host_suffixes

    async def materialize(
        self,
        result: ParseResult,
        client: httpx.AsyncClient,
        referer: str,
    ) -> ParseResult:
        image_number = 0
        try:
            if result.ordered_contents:
                candidates = []
                for item in result.ordered_contents:
                    if item.kind not in {"image", "image_error"}:
                        continue
                    image_number += 1
                    if item.kind == "image_error" or not item.value:
                        continue
                    if item.value.startswith("base64://"):
                        continue
                    candidates.append((image_number, item, item.value))

                outcomes = await self._download_images(
                    client,
                    [image_url for _, _, image_url in candidates],
                    referer,
                )
                for (number, item, image_url), outcome in zip(
                    candidates, outcomes, strict=True
                ):
                    if isinstance(outcome, Path):
                        image_path = outcome
                        result.temporary_files.append(image_path)
                        result.image_source_urls[str(image_path.resolve())] = image_url
                        item.value = str(image_path)
                    else:
                        detail = self._image_error_detail(outcome)
                        item.kind = "image_error"
                        item.value = f"第 {number} 张图片获取失败：{detail}"
                        logger.warning(
                            f"图片下载失败 ({self._hostname_label(image_url)}): {detail}"
                        )
                return result

            legacy_index = 0
            candidates = []
            for field_name in ("cover_urls", "image_urls"):
                image_values = getattr(result, field_name)
                for field_index, image_url in enumerate(image_values):
                    image_number += 1
                    if not image_url or image_url.startswith("base64://"):
                        legacy_index += 1
                        continue
                    candidates.append(
                        (
                            image_number,
                            legacy_index,
                            image_values,
                            field_index,
                            image_url,
                        )
                    )
                    legacy_index += 1

            outcomes = await self._download_images(
                client,
                [image_url for _, _, _, _, image_url in candidates],
                referer,
            )
            for candidate, outcome in zip(candidates, outcomes, strict=True):
                number, index, image_values, field_index, image_url = candidate
                if isinstance(outcome, Path):
                    image_path = outcome
                    result.temporary_files.append(image_path)
                    result.image_source_urls[str(image_path.resolve())] = image_url
                    image_values[field_index] = str(image_path)
                else:
                    image_values[field_index] = ""
                    detail = self._image_error_detail(outcome)
                    result.image_errors[index] = (
                        f"第 {number} 张图片获取失败：{detail}"
                    )
                    logger.warning(
                        f"图片下载失败 ({self._hostname_label(image_url)}): {detail}"
                    )
            return result
        except Exception:
            cleanup_temporary_files(result)
            raise

    async def _download_images(
        self,
        client: httpx.AsyncClient,
        image_urls: list[str],
        referer: str,
    ) -> list[Path | Exception]:
        """并发下载图片，并按输入顺序返回路径或可恢复错误。"""
        semaphore = asyncio.Semaphore(self._download_concurrency())

        async def download(image_url: str) -> Path | Exception:
            async with semaphore:
                try:
                    return await self._download_image(client, image_url, referer)
                except Exception as exc:
                    return exc

        outcomes = await asyncio.gather(*(download(url) for url in image_urls))
        unexpected = next(
            (
                outcome
                for outcome in outcomes
                if isinstance(outcome, Exception)
                and not isinstance(outcome, (httpx.HTTPError, httpx.InvalidURL))
            ),
            None,
        )
        if unexpected is not None:
            for outcome in outcomes:
                if isinstance(outcome, Path):
                    outcome.unlink(missing_ok=True)
            raise unexpected
        return outcomes

    def _download_concurrency(self) -> int:
        value = self.config.get(
            "image_download_concurrency", self.DEFAULT_DOWNLOAD_CONCURRENCY
        )
        try:
            concurrency = int(value)
        except (TypeError, ValueError):
            concurrency = self.DEFAULT_DOWNLOAD_CONCURRENCY
        return min(max(concurrency, 1), self.MAX_DOWNLOAD_CONCURRENCY)

    async def _download_image(
        self,
        client: httpx.AsyncClient,
        image_url: str,
        referer: str,
    ) -> Path:
        current_url = image_url
        for redirect_count in range(6):
            self._validate_image_url(current_url)
            async with client.stream(
                "GET",
                current_url,
                headers={"Referer": referer},
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if redirect_count >= 5 or not location:
                        raise httpx.InvalidURL("too many image redirects")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                image_path = self._new_image_path(
                    current_url, response.headers.get("Content-Type", "")
                )
                try:
                    with image_path.open("wb") as image_file:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            image_file.write(chunk)
                except Exception:
                    image_path.unlink(missing_ok=True)
                    raise
                return image_path
        raise httpx.InvalidURL("too many image redirects")

    def _new_image_path(self, image_url: str, content_type: str) -> Path:
        configured_dir = self.config.get("image_temp_dir")
        temp_dir = (
            Path(str(configured_dir))
            if configured_dir
            else Path(__file__).resolve().parents[1] / "data" / "temp" / "images"
        )
        temp_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(urlparse(image_url).path).suffix.lower()
        allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
        if suffix not in allowed_suffixes:
            media_type = content_type.split(";", 1)[0].strip().lower()
            suffix = mimetypes.guess_extension(media_type) or ".img"
            if suffix == ".jpe":
                suffix = ".jpg"
        return temp_dir / f"{uuid4().hex}{suffix}"

    def _validate_image_url(self, image_url: str) -> None:
        try:
            parsed_url = urlparse(image_url)
            hostname = parsed_url.hostname
            if (
                parsed_url.scheme not in {"http", "https"}
                or not hostname
                or parsed_url.username is not None
                or parsed_url.password is not None
                or (
                    self.allowed_host_suffixes
                    and not any(
                        hostname == suffix or hostname.endswith(f".{suffix}")
                        for suffix in self.allowed_host_suffixes
                    )
                )
            ):
                raise httpx.InvalidURL("unsafe image URL")
            try:
                port = parsed_url.port
            except ValueError as exc:
                raise httpx.InvalidURL("invalid image URL port") from exc
            if port not in {None, 80, 443}:
                raise httpx.InvalidURL("invalid image URL port")
            try:
                parsed_ip = ipaddress.ip_address(hostname)
            except ValueError:
                lowered_hostname = hostname.lower()
                if lowered_hostname == "localhost" or lowered_hostname.endswith(
                    (".localhost", ".local", ".internal")
                ):
                    raise httpx.InvalidURL("unsafe image hostname") from None
            else:
                if not parsed_ip.is_global:
                    raise httpx.InvalidURL("unsafe image IP")
        except ValueError as exc:
            raise httpx.InvalidURL("invalid image URL") from exc

    @staticmethod
    def _image_error_detail(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        return type(exc).__name__

    @staticmethod
    def _hostname_label(image_url: str) -> str:
        try:
            return urlparse(image_url).hostname or "unknown"
        except ValueError:
            return "unknown"


class VideoMaterializer:
    """使用平台声明的非敏感请求头安全下载远程视频。"""

    MAX_REDIRECTS = 5
    CHUNK_SIZE = 64 * 1024

    def __init__(
        self,
        config: Mapping[str, object],
        allowed_host_suffixes: tuple[str, ...],
    ) -> None:
        self.config = config
        self.allowed_host_suffixes = allowed_host_suffixes

    async def materialize(self, result: ParseResult) -> Path:
        """下载视频、登记临时文件并返回本地路径。"""
        headers = sanitize_media_headers(result.video_download_headers)
        async with httpx.AsyncClient(
            timeout=request_timeout(self.config),
            headers=headers,
            follow_redirects=False,
        ) as client:
            video_path = await self._download(client, result.video_url)
        result.temporary_files.append(video_path)
        return video_path

    async def _download(self, client: httpx.AsyncClient, video_url: str) -> Path:
        current_url = video_url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            self._validate_url(current_url)
            async with client.stream("GET", current_url) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if redirect_count >= self.MAX_REDIRECTS or not location:
                        raise httpx.InvalidURL("too many video redirects")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                max_size_bytes = self._max_size_bytes()
                content_length = response.headers.get("Content-Length", "")
                if (
                    max_size_bytes is not None
                    and content_length.isdigit()
                    and int(content_length) > max_size_bytes
                ):
                    raise ValueError("视频下载大小超过配置限制")

                video_path = self._new_video_path(
                    current_url, response.headers.get("Content-Type", "")
                )
                downloaded_bytes = 0
                try:
                    with video_path.open("wb") as video_file:
                        async for chunk in response.aiter_bytes(
                            chunk_size=self.CHUNK_SIZE
                        ):
                            downloaded_bytes += len(chunk)
                            if (
                                max_size_bytes is not None
                                and downloaded_bytes > max_size_bytes
                            ):
                                raise ValueError("视频下载大小超过配置限制")
                            video_file.write(chunk)
                except Exception:
                    video_path.unlink(missing_ok=True)
                    raise
                return video_path
        raise httpx.InvalidURL("too many video redirects")

    def _validate_url(self, video_url: str) -> None:
        if not self.allowed_host_suffixes or not is_trusted_https_url(
            video_url, self.allowed_host_suffixes
        ):
            raise httpx.InvalidURL("unsafe video URL")

    def _max_size_bytes(self) -> int | None:
        max_size_mb = float(self.config.get("max_video_size_mb", 50))
        return None if max_size_mb <= 0 else int(max_size_mb * 1024 * 1024)

    @staticmethod
    def _new_video_path(video_url: str, content_type: str) -> Path:
        temp_dir = Path(__file__).resolve().parents[1] / "data" / "temp" / "videos"
        temp_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(urlparse(video_url).path).suffix.lower()
        if suffix not in {".m4v", ".mkv", ".mov", ".mp4", ".webm"}:
            media_type = content_type.split(";", 1)[0].strip().lower()
            suffix = mimetypes.guess_extension(media_type) or ".mp4"
        return temp_dir / f"{uuid4().hex}{suffix}"


def cleanup_temporary_files(result: ParseResult) -> None:
    """删除解析结果登记的临时文件，并始终清空登记列表。"""
    for path in result.temporary_files:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"清理临时图片失败 ({path.name}): {exc}")
    result.temporary_files.clear()
    result.image_source_urls.clear()
