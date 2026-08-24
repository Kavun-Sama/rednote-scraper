# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data model for scraped notes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

NOTE_URL = "https://www.rednote.com/discovery/item/{note_id}?xsec_token={token}&xsec_source=pc_search"


@dataclass(slots=True)
class Note:
    """A single search result, normalised across image and video notes."""

    note_id: str
    xsec_token: str
    type: str
    title: str
    author: str
    author_id: str
    liked_count: int
    images: list[str] = field(default_factory=list)
    cover: str = ""
    video: str = ""
    video_backups: list[str] = field(default_factory=list)
    duration: int = 0

    @property
    def url(self) -> str:
        return NOTE_URL.format(note_id=self.note_id, token=self.xsec_token)

    @property
    def media_count(self) -> int:
        return len(self.images) + (1 if self.video else 0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        data["media_count"] = self.media_count
        return data
