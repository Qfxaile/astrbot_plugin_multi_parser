from types import SimpleNamespace

from astrbot_multi_parser.services.container import ServiceContainer


def test_service_container_lazily_creates_conversation_history():
    manager = object()
    container = ServiceContainer(SimpleNamespace(conversation_manager=manager), {})

    assert container.conversation_history.conversation_manager is manager
    assert container.conversation_history is container.conversation_history
