from astrbot_multi_parser.services.forward_link_delivery import (
    ForwardLinkDeliveryService,
)


def test_forward_link_delivery_raw_node_contains_text_component():
    node = ForwardLinkDeliveryService._raw_node("bot", "1", "视频直链")
    assert node["data"]["content"][0]["data"]["text"] == "视频直链"
