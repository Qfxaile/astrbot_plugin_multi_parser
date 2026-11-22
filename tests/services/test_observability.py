import asyncio
import sqlite3
from datetime import datetime

from astrbot_multi_parser.services.observability import PluginObservability


async def test_parse_metrics_and_logs_are_sanitized_and_survive_recreation(tmp_path):
    database = tmp_path / "plugin" / "observability.sqlite3"
    service = PluginObservability(database)
    service.record_parse(
        "douyin",
        "success",
        18,
        sent=True,
        source="aiocqhttp · 群聊",
        umo="bot:GroupMessage:123_456",
        group_id="456",
        links="https://example.com/post?token=secret&foo=bar",
        result="解析成功",
    )
    service.record_parse("wechat", "skipped", 3)
    service.record_parse(
        "bilibili", "failure", 9, error_type="ValueError", error_reason="请求失败"
    )

    await service.flush()
    reopened = PluginObservability(database)
    dashboard = await reopened.dashboard()
    logs = (await reopened.logs())["rows"]
    assert dashboard["totals"]["parses"] == 1
    assert dashboard["totals"]["sent"] == 1
    assert dashboard["totals"]["skipped"] == 1
    assert dashboard["totals"]["failures"] == 1
    assert logs[0]["platform"] == "bilibili"
    assert logs[0]["status"] == "failure"
    assert logs[0]["error_type"] == "ValueError"
    success = next(row for row in logs if row["platform"] == "douyin")
    assert success["source"] == "aiocqhttp · 群聊"
    assert success["umo"] == "bot:GroupMessage:123_456"
    assert success["group_id"] == "456"
    assert success["links"] == [
        "https://example.com/post?token=%5B%E5%B7%B2%E9%9A%90%E8%97%8F%5D&foo=%5B%E5%B7%B2%E9%9A%90%E8%97%8F%5D"
    ]
    assert success["result"] == "解析成功"
    assert "secret" not in str(success)
    assert not {"message", "sender_id", "cookie"} & success.keys()


async def test_existing_log_database_keeps_rows_when_adding_session_fields(tmp_path):
    database = tmp_path / "observability.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """CREATE TABLE parse_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL, day TEXT NOT NULL,
                platform TEXT NOT NULL, status TEXT NOT NULL,
                duration_ms INTEGER NOT NULL, error_type TEXT NOT NULL,
                source TEXT NOT NULL, links_json TEXT NOT NULL,
                result TEXT NOT NULL, error_reason TEXT NOT NULL
            );
            INSERT INTO parse_logs VALUES (1, '2000-01-01T12:00:00+09:00',
                '2000-01-01', 'douyin', 'success', 9, '', 'aiocqhttp · 群聊',
                '[]', '解析成功', '');"""
        )
    service = PluginObservability(database)
    service.record_parse(
        "douyin", "success", 18, source="aiocqhttp · 私聊", umo="bot:FriendMessage:123"
    )

    await service.flush()
    new, old = (await PluginObservability(database).logs())["rows"]
    assert new["umo"] == "bot:FriendMessage:123"
    assert new["group_id"] == ""
    assert old["source"] == "aiocqhttp · 群聊"
    assert old["umo"] == old["group_id"] == ""


async def test_links_remove_credentials_fragments_and_sensitive_query_values(tmp_path):
    service = PluginObservability(tmp_path / "observability.sqlite3")
    service.record_parse(
        "test",
        "failure",
        1,
        links=(
            "https://user:pass@example.com/a?a=private "
            "https://example.com/b?next=https%3A%2F%2Fexample.com%2Fprivate#private-fragment "
            "https://example.com/c https://example.com/d https://example.com/e"
        ),
        error_reason='failed at https://example.com/?token=private: {"Authorization": "Bearer private"}',
    )

    await service.flush()
    row = (await service.logs())["rows"][0]
    assert [link.split("?")[0] for link in row["links"]] == [
        "https://example.com/b",
        "https://example.com/c",
        "https://example.com/d",
    ]
    assert "private" not in str(row)


async def test_logs_have_no_upper_limit(tmp_path):
    service = PluginObservability(tmp_path / "observability.sqlite3")
    for index in range(520):
        service.record_parse("douyin", "success", index)

    await service.flush()
    logs = await service.logs()
    assert logs["matched"] == 520
    assert len(logs["rows"]) == 50
    assert logs["rows"][0]["duration_ms"] == 519
    last = await service.logs({"page": 1000000000000000000000})
    assert last["page"] == 11
    assert [row["duration_ms"] for row in last["rows"]] == list(range(19, -1, -1))
    assert (await service.dashboard())["totals"]["parses"] == 520


async def test_log_filters_search_all_pages_and_combine_with_result_and_date(tmp_path):
    service = PluginObservability(tmp_path / "observability.sqlite3")
    today = datetime.now().astimezone().date().isoformat()
    for platform in ("bilibili", "douyin"):
        service.record_parse(
            platform,
            "failure",
            3,
            source="qq · 群聊",
            umo="ONE:GroupMessage:233",
            error_reason="请求超时",
        )
    for _ in range(51):
        service.record_parse("douyin", "success", 1)
    await service.flush()

    result = await service.logs(
        {
            "source": "qq · 群聊",
            "platform": "douyin",
            "status": "failure",
            "query": "one:",
            "start": today,
            "end": today,
        }
    )
    assert result["total"] == 53
    assert result["matched"] == 1
    assert result["counts"] == {"success": 0, "failure": 1, "skipped": 0}
    (row,) = result["rows"]
    assert row["error_reason"] == "请求超时"
    assert result["platforms"] == ["bilibili", "douyin"]
    assert (await service.logs({"query": "%' OR 1=1 --"}))["matched"] == 0


async def test_concurrent_async_writes_keep_log_and_daily_counts_atomic(tmp_path):
    service = PluginObservability(tmp_path / "observability.sqlite3")
    other = PluginObservability(service.database_path)
    for index in range(32):
        target = service if index % 2 else other
        target.record_parse("douyin", "success", index, sent=True)
    await asyncio.gather(service.flush(), other.flush())

    dashboard = await service.dashboard("today")
    assert dashboard["totals"]["parses"] == 32
    assert dashboard["totals"]["sent"] == 32
    assert (await service.logs())["matched"] == 32


async def test_ai_usage_is_persisted_with_provider_counts_or_estimates(tmp_path):
    database = tmp_path / "observability.sqlite3"
    service = PluginObservability(database)
    service.record_ai_usage("abcdef", "xyz", exact_input=12, exact_output=4)
    service.record_ai_usage("abcdef", "xyz", images=1)

    await service.flush()
    totals = (await PluginObservability(database).dashboard())["totals"]
    assert totals["tokens"] == 19
    assert totals["input_tokens"] == 14
    assert totals["output_tokens"] == 5
    assert totals["exact_token_calls"] == 1
    assert totals["estimated_token_calls"] == 1
    assert totals["image_calls"] == 1


async def test_dashboard_periods_have_expected_order_and_range(tmp_path):
    service = PluginObservability(tmp_path / "observability.sqlite3")

    today = await service.dashboard("today")
    week = await service.dashboard("7d")
    fortnight = await service.dashboard("14d")
    month = await service.dashboard("30d")
    all_time = await service.dashboard("all")

    assert today["period"] == "today" and len(today["days"]) == 1
    assert week["period"] == "7d" and len(week["days"]) == 7
    assert fortnight["period"] == "14d" and len(fortnight["days"]) == 14
    assert month["period"] == "30d" and len(month["days"]) == 30
    assert all_time["period"] == "all" and all_time["days"]
    assert [item["date"] for item in week["days"]] == sorted(
        item["date"] for item in week["days"]
    )
