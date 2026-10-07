from astrbot.api.message_components import Plain
from astrbot_multi_parser.services.delivery.link_filter import LinkFilter


def test_link_filter_changes_plain_text_but_preserves_other_components():
    image = object()
    components = [Plain("打开 https://example.com/a"), image]

    filtered = LinkFilter({"filter_output_links": True}).apply(components)

    assert isinstance(filtered[0], Plain)
    assert "https://" not in filtered[0].text
    assert filtered[1] is image
