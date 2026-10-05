"""插件应用服务的统一装配容器。"""

from collections.abc import Mapping

from .ai_summary import AISummaryService
from .authentication import AuthenticationService
from .conversation_history import ConversationHistoryService
from .delivery import DeliveryService


class ServiceContainer:
    """集中拥有插件级服务实例，避免入口类维护多组懒加载逻辑。"""

    def __init__(self, context, config: Mapping[str, object]):
        self.context = context
        self.config = config
        self.delivery = DeliveryService(config)
        self.authentication = AuthenticationService(config)
        self.ai_summary = AISummaryService(context, config)
        self._conversation_history = None

    @property
    def conversation_history(self) -> ConversationHistoryService:
        if self._conversation_history is None:
            self._conversation_history = ConversationHistoryService(
                self.context.conversation_manager
            )
        return self._conversation_history
