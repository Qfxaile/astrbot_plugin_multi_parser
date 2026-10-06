"""番茄小说解析领域模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NovelMetadata:
    title: str = ""
    author: str = ""
    description: str = ""
    cover_url: str = ""

    def with_fallback(self, fallback: "NovelMetadata") -> "NovelMetadata":
        return NovelMetadata(
            title=self.title or fallback.title,
            author=self.author or fallback.author,
            description=self.description or fallback.description,
            cover_url=self.cover_url or fallback.cover_url,
        )
