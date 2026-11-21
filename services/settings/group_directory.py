"""从指定 QQ 实例读取用于 WebUI 选择的群聊目录。"""

import asyncio
from collections.abc import Mapping

from astrbot.api import logger
from astrbot.core.platform.message_type import MessageType

from ..event_identity import QQ_ADAPTERS


class GroupDirectory:
    """封装 QQ 群目录和 AstrBot 适配器元数据的读取。"""

    def __init__(self, context):
        self.context = context

    def _instances(self):
        manager = getattr(self.context, "platform_manager", None)
        for adapter in getattr(manager, "platform_insts", ()):
            try:
                meta = adapter.meta()
                if isinstance(meta.id, str) and meta.id:
                    yield meta, adapter
            except Exception as exc:
                logger.debug(f"读取适配器信息失败: {type(exc).__name__}")

    def qq_adapters(self) -> list[dict[str, str]]:
        """返回支持群聊选择的 QQ 实例和显示名称。"""
        return [
            {
                "id": meta.id,
                "name": getattr(meta, "adapter_display_name", None)
                or ("OneBot" if meta.name == "aiocqhttp" else "QQ 官方"),
                "type": meta.name,
            }
            for meta, _ in self._instances()
            if meta.name in QQ_ADAPTERS
        ]

    async def list_groups(self, instance: str) -> dict:
        """OneBot 查询群列表，QQ 官方仅返回适配器已观测的群聊。"""
        for meta, adapter in self._instances():
            if meta.id == instance and meta.name in QQ_ADAPTERS:
                if meta.name == "aiocqhttp":
                    rows = await asyncio.wait_for(
                        adapter.get_client().call_action("get_group_list"), timeout=8
                    )
                    if not isinstance(rows, list):
                        raise RuntimeError("群列表返回格式无效")
                    return {"source": "onebot", "groups": _groups(instance, rows)}
                scenes = getattr(adapter, "_session_scene", None)
                rows = (
                    (
                        {"group_id": key}
                        for key, scene in scenes.items()
                        if scene == "group"
                    )
                    if isinstance(scenes, Mapping)
                    else ()
                )
                return {"source": "observed", "groups": _groups(instance, rows)}
        raise LookupError("找不到所选 QQ 适配器实例")


def _groups(instance, rows):
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = row.get("group_id")
        if type(value) not in (str, int):
            continue
        group_id = str(value)
        if not group_id or len(group_id) > 256 or not group_id.isalnum():
            continue
        label = row.get("group_name")
        label = (
            "".join(c for c in label if c.isprintable()).strip()[:80]
            if isinstance(label, str)
            else ""
        )
        result[group_id] = {
            "id": group_id,
            "umo": f"{instance}:{MessageType.GROUP_MESSAGE.value}:{group_id}",
            "label": label,
        }
    return list(result.values())
