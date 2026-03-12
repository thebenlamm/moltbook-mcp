"""Engagement logger for Moltbook interactions.

Appends structured entries to a configurable engagement log.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MOLTBOOK_LOG = Path(os.environ.get(
    "MOLTBOOK_LOG_PATH",
    str(Path.home() / ".config" / "moltbook" / "engagement.md"),
))


def log_engagement(
    action: str,
    post_id: Optional[str] = None,
    submolt: Optional[str] = None,
    content_preview: Optional[str] = None,
    parent_context: Optional[str] = None,
) -> None:
    """Append an engagement entry to the Moltbook log.

    Args:
        action: Type of action (post, comment, upvote, downvote, follow, unfollow)
        post_id: Post or comment ID
        submolt: Submolt name if applicable
        content_preview: First 100 chars of content
        parent_context: Parent comment ID if replying
    """
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        parts = [f"- **{timestamp}** | {action}"]
        if submolt:
            parts.append(f"m/{submolt}")
        if post_id:
            parts.append(f"ID: {post_id}")
        if parent_context:
            parts.append(f"reply-to: {parent_context}")
        if content_preview:
            preview = content_preview[:100].replace("\n", " ").strip()
            parts.append(f'"{preview}"')

        entry = " | ".join(parts) + "\n"

        if not MOLTBOOK_LOG.exists():
            MOLTBOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
            MOLTBOOK_LOG.write_text("# Moltbook Engagement Log\n\n")

        with open(MOLTBOOK_LOG, "a") as f:
            f.write(entry)

        logger.info(f"Logged engagement: {action} {post_id or ''}")
    except Exception as e:
        logger.error(f"Failed to log engagement: {e}")
