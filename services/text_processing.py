"""提供消息投递前的纯文本处理能力。"""

import re

LINK_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\[\]()（）]+", re.IGNORECASE)
LINK_TRAILING_PUNCTUATION = ".,!?;:，。！？；："


def replace_links(
    text: str,
    replacement: str = "[详细内容请打开原链接查看]",
) -> str:
    """替换可见文本中的网页链接，并保留链接末尾的标点。"""

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        link = value.rstrip(LINK_TRAILING_PUNCTUATION)
        trailing = value[len(link) :]
        return f"{replacement}{trailing}"

    return LINK_PATTERN.sub(replace, text).strip()
