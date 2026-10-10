from astrbot.api.message_components import Node, Plain
from astrbot_multi_parser.services.content_assembly import ContentAssembler


def test_content_assembler_merges_text_and_balances_nodes():
    merged = ContentAssembler.merge_adjacent_plain([Plain("a"), Plain("b")])
    assert [item.text for item in merged] == ["a\nb"]

    nodes = [Node(content=[Plain(str(index))]) for index in range(201)]
    batches = ContentAssembler.balanced_forward_batches(nodes)
    assert [len(batch) for batch in batches] == [67, 67, 67]
