from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_multi_parser.services.settings.group_directory import GroupDirectory


def adapter(instance, kind, **values):
    return SimpleNamespace(
        meta=lambda: SimpleNamespace(id=instance, name=kind), **values
    )


def directory(*adapters):
    return GroupDirectory(
        SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=adapters))
    )


async def test_query_selected_onebot_and_return_only_group_identifiers_and_names():
    call = AsyncMock(
        return_value=[
            {"group_id": 88, "group_name": "开\n发群", "unrelated": "secret"},
            {"group_id": "88", "group_name": "开发群"},
            {"group_id": True},
            None,
        ]
    )
    another = AsyncMock()
    service = directory(
        adapter(
            "qq-a", "aiocqhttp", get_client=lambda: SimpleNamespace(call_action=call)
        ),
        adapter(
            "qq-b", "aiocqhttp", get_client=lambda: SimpleNamespace(call_action=another)
        ),
        adapter("slack", "slack"),
    )
    assert [item["id"] for item in service.qq_adapters()] == ["qq-a", "qq-b"]
    assert await service.list_groups("qq-a") == {
        "source": "onebot",
        "groups": [{"id": "88", "umo": "qq-a:GroupMessage:88", "label": "开发群"}],
    }
    call.assert_awaited_once_with("get_group_list")
    another.assert_not_called()
    with pytest.raises(LookupError):
        await service.list_groups("slack")


@pytest.mark.parametrize("kind", ["qq_official", "qq_official_webhook"])
async def test_qq_official_lists_observed_groups_only(kind):
    service = directory(
        adapter(
            "qq", kind, _session_scene={"G1": "group", "U1": "friend", "C1": "channel"}
        )
    )
    assert await service.list_groups("qq") == {
        "source": "observed",
        "groups": [{"id": "G1", "umo": "qq:GroupMessage:G1", "label": ""}],
    }


async def test_invalid_result_is_not_reported_as_empty_group_list():
    service = directory(
        adapter(
            "qq",
            "aiocqhttp",
            get_client=lambda: SimpleNamespace(
                call_action=AsyncMock(return_value=None)
            ),
        )
    )
    with pytest.raises(RuntimeError, match="格式"):
        await service.list_groups("qq")
