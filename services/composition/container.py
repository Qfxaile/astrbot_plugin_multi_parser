"""插件应用服务的统一装配容器。"""

from collections.abc import Mapping
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from ..authentication.service import AuthenticationService
from ..conversation.history import ConversationHistoryService
from ..delivery.service import DeliveryService
from ..observability import PluginObservability
from ..summary.service import AISummaryService


class ServiceContainer:
    """集中拥有插件级服务实例，避免入口类维护多组懒加载逻辑。"""

    def __init__(
        self,
        context,
        config: Mapping[str, object],
        observability_path: Path | None = None,
    ):
        self.context = context
        self.config = config
        self.delivery = DeliveryService(config)
        self.authentication = AuthenticationService(config)
        self.observability = PluginObservability(
            observability_path
            or Path(get_astrbot_plugin_data_path())
            / "astrbot_plugin_multi_parser"
            / "observability.sqlite3"
        )
        self.ai_summary = AISummaryService(context, config, self.observability)
        self._conversation_history = None

    @property
    def conversation_history(self) -> ConversationHistoryService:
        if self._conversation_history is None:
            self._conversation_history = ConversationHistoryService(
                self.context.conversation_manager
            )
        return self._conversation_history
