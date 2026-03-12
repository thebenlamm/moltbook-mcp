"""Inbound content sanitization — wraps user-generated content to prevent prompt injection."""

import re
from typing import Any

# Keys that contain user-generated content worth sanitizing.
# Deliberately excludes "text" (too generic — would corrupt error messages and challenges).
_CONTENT_KEYS = {"title", "content", "body"}

# Keys to skip entirely (metadata, never user-generated)
_SKIP_KEYS = {"id", "author", "created_at", "updated_at", "score", "comment_count", "type"}

_MARKER_RE = re.compile(r"\[USER_CONTENT_(?:START|END)\]")


def sanitize_text(text: str) -> str:
    """Strip existing markers, then wrap in fresh ones."""
    if not isinstance(text, str) or not text.strip():
        return text
    cleaned = _MARKER_RE.sub("", text)
    return f"[USER_CONTENT_START]{cleaned}[USER_CONTENT_END]"


def sanitize_response(data: Any) -> Any:
    """Recursively walk API response and sanitize user-generated content fields."""
    if isinstance(data, dict):
        return {
            k: sanitize_text(v) if k in _CONTENT_KEYS and isinstance(v, str)
            else sanitize_response(v) if k not in _SKIP_KEYS
            else v
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [sanitize_response(item) for item in data]
    return data
