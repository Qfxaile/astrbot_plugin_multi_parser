"""持久化插件解析日志、每日统计和 AI Token 用量。"""

import asyncio
import json
import os
import re
import sqlite3
from collections import Counter, deque
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timedelta
from math import ceil
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from astrbot.api import logger

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SENSITIVE_VALUE = re.compile(
    r"(?is)\b(?:set[-_ ]?cookie|cookies?|token|secret|passw(?:or)?d|authorization|"
    r"session(?:_?id)?|api[-_ ]?key|bearer)\b[\"']?\s*(?:[:=]|(?<=bearer)\s)\s*.+"
)
_METRIC_COLUMNS = (
    "parses",
    "sent",
    "failures",
    "skipped",
    "tokens",
    "input_tokens",
    "output_tokens",
    "ai_calls",
    "exact_token_calls",
    "estimated_token_calls",
    "image_calls",
)
_RANGES = {"today": 1, "7d": 7, "14d": 14, "30d": 30}
_LOG_PAGE_SIZE = 50
_LOG_STATUSES = ("success", "failure", "skipped")
_Write = Callable[[sqlite3.Connection], None]


class PluginObservability:
    """以 SQLite 保存解析运行日志和按日统计，数据库操作不阻塞事件循环。"""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._pending: deque[_Write] = deque()
        self._writer: asyncio.Task[None] | None = None

    def record_parse(
        self,
        platform: str,
        status: str,
        duration_ms: int,
        *,
        sent: bool = False,
        error_type: str = "",
        source: str = "",
        umo: str = "",
        group_id: str = "",
        links: str = "",
        result: str = "",
        error_reason: str = "",
    ) -> None:
        """异步记录解析日志，并在同一事务内递增每日统计。"""
        now = datetime.now().astimezone()
        metrics = Counter()
        if status == "success":
            metrics["parses"] = 1
            metrics["sent"] = int(sent)
        elif status == "failure":
            metrics["failures"] = 1
        else:
            metrics["skipped"] = 1

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    """INSERT INTO parse_logs
                    (created_at, day, platform, status, duration_ms, error_type,
                     source, links_json, result, error_reason, umo, group_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now.isoformat(timespec="seconds"),
                        now.date().isoformat(),
                        _safe_text(platform, 100),
                        status,
                        max(0, duration_ms),
                        _safe_text(error_type, 100),
                        _safe_text(source, 100),
                        json.dumps(_safe_links(links), ensure_ascii=False),
                        _safe_text(result, 160),
                        _safe_text(error_reason, 240),
                        _safe_text(umo, 1024),
                        _safe_text(group_id, 256),
                    ),
                )
                _increment_daily(connection, now.date(), metrics)

        self._enqueue(write)

    def record_ai_usage(
        self,
        prompt: str,
        completion: str,
        *,
        images: int = 0,
        exact_input: int | None = None,
        exact_output: int | None = None,
    ) -> None:
        """异步持久化 Provider 精确用量或文本字符数估算值。"""
        input_tokens = (
            exact_input if exact_input is not None else _estimate_tokens(prompt)
        )
        output_tokens = (
            exact_output if exact_output is not None else _estimate_tokens(completion)
        )
        now = datetime.now().astimezone()
        metrics = Counter(
            tokens=input_tokens + output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            ai_calls=1,
            exact_token_calls=int(exact_input is not None and exact_output is not None),
            estimated_token_calls=int(exact_input is None or exact_output is None),
            image_calls=int(bool(images)),
        )

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                connection.execute(
                    """INSERT INTO ai_usage (created_at, day, input_tokens,
                    output_tokens, exact, images) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        now.isoformat(timespec="seconds"),
                        now.date().isoformat(),
                        input_tokens,
                        output_tokens,
                        int(exact_input is not None and exact_output is not None),
                        int(bool(images)),
                    ),
                )
                _increment_daily(connection, now.date(), metrics)

        self._enqueue(write)

    async def dashboard(self, period: str = "14d") -> dict[str, object]:
        """返回所选时间范围内的完整工作台汇总和每日序列。"""
        if not isinstance(period, str) or period not in {*_RANGES, "all"}:
            period = "14d"
        today = datetime.now().astimezone().date()
        days = _RANGES.get(period)
        start = today - timedelta(days=days - 1) if days else None

        def read(connection: sqlite3.Connection) -> dict[str, object]:
            if period == "all":
                first = connection.execute(
                    "SELECT MIN(day) FROM daily_stats"
                ).fetchone()[0]
                first_day = date.fromisoformat(first) if first else today
            else:
                first_day = start
            condition = "WHERE day >= ?" if start else ""
            params = (start.isoformat(),) if start else ()
            rows = connection.execute(
                f"SELECT * FROM daily_stats {condition} ORDER BY day", params
            ).fetchall()
            by_day = {row["day"]: row for row in rows}
            totals = Counter()
            series = []
            cursor = first_day
            while cursor <= today:
                stored = by_day.get(cursor.isoformat())
                values = {
                    key: int(stored[key]) if stored else 0 for key in _METRIC_COLUMNS
                }
                totals.update(values)
                series.append({"date": cursor.isoformat(), **values})
                cursor += timedelta(days=1)
            return {
                "period": period,
                "days": series,
                "totals": {key: totals[key] for key in _METRIC_COLUMNS},
                "token_note": "Provider 返回精确用量时使用精确值，否则按文本字符数估算；估算不含图片 Token。",
            }

        return await self._query(read)

    async def logs(self, filters: dict[str, object] | None = None) -> dict[str, object]:
        """在 SQLite 内筛选完整历史，只返回按时间倒序排列的一页。"""
        filters = filters or {}
        page = filters.get("page", 1)
        page = max(1, page) if type(page) is int else 1
        conditions, params = [], []
        for key, operator in (("start", ">="), ("end", "<=")):
            value = filters.get(key)
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                conditions.append(f"day {operator} ?")
                params.append(value)
        for key in ("source", "platform", "status"):
            value = filters.get(key)
            if isinstance(value, str) and value:
                conditions.append(f"{key} = ?")
                params.append(value[:100])
        if isinstance(query := filters.get("query"), str) and (query := query.strip()):
            conditions.append(
                "(instr(lower(umo), ?) OR instr(lower(group_id), ?) OR "
                "instr(lower(links_json), ?) OR instr(lower(error_reason), ?) OR "
                "instr(lower(error_type), ?))"
            )
            params.extend([query[:512].lower()] * 5)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        def read(connection: sqlite3.Connection) -> dict[str, object]:
            # 计数、筛选选项与当前页取自同一快照，写入可以继续进行。
            connection.execute("BEGIN")
            totals = dict.fromkeys(_LOG_STATUSES, 0)
            matched = 0
            for status, count in connection.execute(
                f"SELECT status, COUNT(*) FROM parse_logs {where} GROUP BY status",
                params,
            ):
                if status in totals:
                    totals[status] = count
                matched += count
            current = min(
                page, max(1, (matched + _LOG_PAGE_SIZE - 1) // _LOG_PAGE_SIZE)
            )
            rows = connection.execute(
                f"""SELECT created_at, platform, status, duration_ms, error_type,
                source, links_json, result, error_reason, umo, group_id FROM parse_logs
                {where} ORDER BY julianday(created_at) DESC, id DESC LIMIT ? OFFSET ?""",
                (*params, _LOG_PAGE_SIZE, (current - 1) * _LOG_PAGE_SIZE),
            ).fetchall()
            total = (
                connection.execute("SELECT COUNT(*) FROM parse_logs").fetchone()[0]
                if conditions
                else matched
            )
            return {
                "rows": [_log_row(row) for row in rows],
                "page": current,
                "page_size": _LOG_PAGE_SIZE,
                "matched": matched,
                "total": total,
                "counts": totals,
                "sources": _log_options(connection, "source"),
                "platforms": _log_options(connection, "platform"),
            }

        return await self._query(read)

    def _enqueue(self, operation: _Write) -> None:
        self._pending.append(operation)
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending:
            await self._persist(self._pending.popleft())

    async def flush(self) -> None:
        """在插件卸载前等待已排队的日志和用量写入。"""
        if self._writer is not None:
            await asyncio.shield(self._writer)

    async def _persist(self, operation: _Write) -> None:
        try:
            await self._ensure_initialized()
            await asyncio.to_thread(self._execute, operation)
        except Exception as exc:
            logger.warning(f"插件解析统计写入 SQLite 失败: {type(exc).__name__}")

    async def _query(self, operation):
        await self._ensure_initialized()
        return await asyncio.to_thread(self._execute, operation)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if not self._initialized:
                for attempt in range(4):
                    try:
                        await asyncio.to_thread(self._initialize)
                    except sqlite3.OperationalError as exc:
                        # 切换 WAL 时可能直接返回锁冲突，不走 SQLite 的 busy timeout。
                        if attempt == 3 or "database is locked" not in str(exc):
                            raise
                        await asyncio.sleep(0.05 * 2**attempt)
                    else:
                        self._initialized = True
                        break

    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _restrict_owned_path(self.database_path.parent, 0o700)
        with (
            closing(sqlite3.connect(self.database_path, timeout=5)) as connection,
            connection,
        ):
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                connection.execute("PRAGMA journal_mode = WAL").fetchone()
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS parse_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    day TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    error_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    links_json TEXT NOT NULL,
                    result TEXT NOT NULL,
                    error_reason TEXT NOT NULL,
                    umo TEXT NOT NULL DEFAULT '',
                    group_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_parse_logs_day_id
                    ON parse_logs(day, id DESC);
                CREATE INDEX IF NOT EXISTS idx_parse_logs_time_id
                    ON parse_logs(julianday(created_at) DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_parse_logs_source ON parse_logs(source);
                CREATE INDEX IF NOT EXISTS idx_parse_logs_platform ON parse_logs(platform);
                CREATE INDEX IF NOT EXISTS idx_parse_logs_status ON parse_logs(status);
                CREATE TABLE IF NOT EXISTS ai_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    day TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    exact INTEGER NOT NULL,
                    images INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_stats (
                    day TEXT PRIMARY KEY,
                    parses INTEGER NOT NULL DEFAULT 0,
                    sent INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    ai_calls INTEGER NOT NULL DEFAULT 0,
                    exact_token_calls INTEGER NOT NULL DEFAULT 0,
                    estimated_token_calls INTEGER NOT NULL DEFAULT 0,
                    image_calls INTEGER NOT NULL DEFAULT 0
                );"""
            )
            # 在写事务内检查旧表，避免并发初始化重复添加字段。
            connection.execute("BEGIN IMMEDIATE")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(parse_logs)")
            }
            for column in ("umo", "group_id"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE parse_logs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
        _restrict_owned_path(self.database_path, 0o600)

    def _execute(self, operation):
        with closing(sqlite3.connect(self.database_path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            return operation(connection)


def _restrict_owned_path(path: Path, mode: int) -> None:
    getuid = getattr(os, "getuid", None)
    if getuid is not None and path.stat().st_uid == getuid():
        path.chmod(mode)


def _log_row(row: sqlite3.Row) -> dict[str, object]:
    entry = dict(row)
    entry["time"] = entry.pop("created_at")
    entry["links"] = json.loads(entry.pop("links_json"))
    return entry


def _log_options(connection: sqlite3.Connection, column: str) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            f"SELECT DISTINCT {column} FROM parse_logs WHERE {column} <> '' ORDER BY {column}"
        )
    ]


def _increment_daily(
    connection: sqlite3.Connection, day: date, increments: Counter
) -> None:
    values = {key: int(increments[key]) for key in _METRIC_COLUMNS}
    columns = ", ".join(_METRIC_COLUMNS)
    placeholders = ", ".join("?" for _ in _METRIC_COLUMNS)
    updates = ", ".join(f"{key} = {key} + excluded.{key}" for key in _METRIC_COLUMNS)
    connection.execute(
        f"INSERT INTO daily_stats (day, {columns}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(day) DO UPDATE SET {updates}",
        (day.isoformat(), *(values[key] for key in _METRIC_COLUMNS)),
    )


def _estimate_tokens(value: str) -> int:
    """以每 3 个字符约 1 Token 给出保守的跨语言粗略估算。"""
    return ceil(len(value) / 3) if value else 0


def _safe_text(value: str, limit: int) -> str:
    value = _URL_PATTERN.sub("[链接]", str(value or ""))
    value = _SENSITIVE_VALUE.sub("[凭据已隐藏]", value)
    return " ".join(value.split())[:limit]


def _safe_links(value: str) -> list[str]:
    links = []
    for match in _URL_PATTERN.finditer(str(value or "")):
        raw = match.group(0).rstrip(".,，。;；:：!?！？)]}》」』")
        try:
            parsed = urlsplit(raw)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                continue
            if parsed.username is not None or parsed.password is not None:
                continue
            host = parsed.hostname.encode("idna").decode("ascii")
            if ":" in host:
                host = f"[{host}]"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            # 未知参数也可能带嵌套凭据或签名，日志不需要保留任何查询值。
            query = [
                (key, "[已隐藏]")
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            ]
            sanitized = urlunsplit(
                (parsed.scheme.lower(), host, parsed.path, urlencode(query), "")
            )
        except (UnicodeError, ValueError):
            continue
        if sanitized not in links:
            links.append(sanitized[:512])
        if len(links) >= 3:
            break
    return links


def usage_counts(response) -> tuple[int | None, int | None]:
    """优先读取 AstrBot 标准用量（含缓存输入），兼容 Provider 的原始字段。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        read = usage.get
    else:

        def read(key, default=None):
            return getattr(usage, key, default)

    input_tokens = _nonnegative_int(
        read(
            "input",
            read("prompt_tokens", read("input_tokens", read("prompt_token_count"))),
        )
    )
    output_tokens = _nonnegative_int(
        read(
            "output",
            read(
                "completion_tokens",
                read("output_tokens", read("candidates_token_count")),
            ),
        )
    )
    return input_tokens, output_tokens


def _nonnegative_int(value) -> int | None:
    return value if type(value) is int and value >= 0 else None
