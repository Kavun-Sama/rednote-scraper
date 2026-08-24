"""Normalisation of raw API payloads into :class:`Note` objects."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from .models import Note

ORIGINAL_IMAGE_HOST = "https://sns-img-qc.xhscdn.com"

_INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL
)
_UNDEFINED_RE = re.compile(r"(?<![\w\"])undefined(?![\w\"])")


def original_image_url(cdn_url: str) -> str:
    """Rewrite a CDN preview URL into its full-resolution original.

    Preview URLs look like ``.../<date>/<hash>/<file-id>!<variant>``; the file
    id alone, served from the origin host, yields the unprocessed upload
    (typically an order of magnitude larger).
    """
    path = urlsplit(cdn_url).path.lstrip("/")
    segments = path.split("/")
    if len(segments) > 2:
        path = "/".join(segments[2:])
    return f"{ORIGINAL_IMAGE_HOST}/{path.split('!')[0]}"


def parse_search_item(item: dict[str, Any], *, originals: bool = True) -> Note | None:
    """Convert one ``search/notes`` item into a :class:`Note`, or ``None``."""
    if item.get("model_type") != "note":
        return None
    card = item.get("note_card") or {}
    user = card.get("user") or {}
    interact = card.get("interact_info") or {}

    images: list[str] = []
    for image in card.get("image_list") or []:
        url = _pick_image_url(image)
        if url:
            images.append(original_image_url(url) if originals else url)

    cover = _pick_image_url(card.get("cover") or {})
    return Note(
        note_id=item["id"],
        xsec_token=item.get("xsec_token", ""),
        type=card.get("type", "normal"),
        title=card.get("display_title", ""),
        author=user.get("nickname") or user.get("nick_name", ""),
        author_id=user.get("user_id", ""),
        liked_count=_to_int(interact.get("liked_count")),
        images=images,
        cover=original_image_url(cover) if originals and cover else cover,
    )


def parse_note_page(html: str, note_id: str) -> dict[str, Any] | None:
    """Extract a note's detail payload from the server-rendered item page."""
    match = _INITIAL_STATE_RE.search(html)
    if match is None:
        return None
    try:
        state = json.loads(_UNDEFINED_RE.sub("null", match.group(1)))
    except json.JSONDecodeError:
        return None
    detail = (state.get("note") or {}).get("noteDetailMap") or {}
    return (detail.get(note_id) or {}).get("note")


def apply_video_detail(note: Note, detail: dict[str, Any]) -> Note:
    """Fill in video stream URLs from a detail payload."""
    media = (detail.get("video") or {}).get("media") or {}
    streams = media.get("stream") or {}
    candidates = [
        stream
        for codec in ("h264", "h265", "av1", "h266")
        for stream in streams.get(codec) or []
    ]
    if not candidates:
        return note
    best = max(candidates, key=lambda s: s.get("size") or 0)
    note.video = best.get("masterUrl", "")
    note.video_backups = list(best.get("backupUrls") or [])
    note.duration = int((media.get("video") or {}).get("duration") or 0)
    return note


def _pick_image_url(image: dict[str, Any]) -> str:
    """Prefer the default-scene variant, falling back to whatever is present."""
    info_list = image.get("info_list") or image.get("infoList") or []
    by_scene = {
        info.get("image_scene") or info.get("imageScene"): info.get("url")
        for info in info_list
    }
    return (
        by_scene.get("WB_DFT")
        or by_scene.get("WB_PRV")
        or image.get("url_default")
        or image.get("url")
        or next((url for url in by_scene.values() if url), "")
    )


def _to_int(value: Any) -> int:
    """Parse a like counter, including the Chinese 万/亿 shorthand."""
    text = str(value or "").strip().replace("+", "").replace(",", "")
    multiplier = 1
    if text.endswith(("万", "w", "W")):
        text, multiplier = text[:-1], 10_000
    elif text.endswith("亿"):
        text, multiplier = text[:-1], 100_000_000
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0
