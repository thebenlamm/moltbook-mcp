"""Privacy filter for outgoing Moltbook content.

Scans posts and comments before submission to prevent
leaking identifiable information as defined in user-configured patterns.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".config" / "moltbook"
_PATTERNS_PATH = _CONFIG_DIR / "privacy-patterns.json"
_REJECTION_LOG = _CONFIG_DIR / "privacy-rejections.md"

_compiled: list[re.Pattern] = []


def _load_patterns() -> list[re.Pattern]:
    """Load privacy patterns from ~/.config/moltbook/privacy-patterns.json.

    Expected format: a flat JSON array of regex strings, e.g.
        ["\\bjohn\\s+doe\\b", "\\bacme\\s+corp\\b"]

    Returns an empty list (filtering disabled) if the file is missing or malformed.
    Individual invalid regexes are skipped with a warning.
    """
    if not _PATTERNS_PATH.exists():
        logger.debug(
            "No privacy patterns file at %s — filtering disabled", _PATTERNS_PATH
        )
        return []

    try:
        raw = json.loads(_PATTERNS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Malformed privacy patterns file %s: %s", _PATTERNS_PATH, e)
        return []

    if not isinstance(raw, list):
        logger.warning("Privacy patterns file must be a JSON array, got %s", type(raw).__name__)
        return []

    compiled = []
    for pattern in raw:
        if not isinstance(pattern, str):
            logger.warning("Skipping non-string privacy pattern: %r", pattern)
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as e:
            logger.warning("Skipping invalid regex %r: %s", pattern, e)
    return compiled


# Load patterns at import time
_compiled = _load_patterns()


def check_content(text: str) -> tuple[bool, Optional[str]]:
    """Check outgoing content for privacy violations.

    Returns:
        (is_safe, reason) — if is_safe is False, reason explains why.
    """
    if not text or not _compiled:
        return True, None

    for pattern in _compiled:
        if pattern.search(text):
            reason = "Content matched a privacy filter pattern"
            _log_rejection(text, reason)
            return False, reason

    return True, None


def _log_rejection(text: str, reason: str) -> None:
    """Log a privacy rejection to ~/.config/moltbook/privacy-rejections.md."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        preview = text[:200].replace("\n", " ")
        entry = f"\n### {timestamp}\n**Reason:** {reason}\n**Preview:** {preview}...\n"

        if not _REJECTION_LOG.exists():
            _REJECTION_LOG.write_text(
                "# Privacy Rejections\n\nContent blocked from posting to Moltbook.\n"
            )

        with open(_REJECTION_LOG, "a") as f:
            f.write(entry)
        logger.warning(f"Privacy rejection: {reason}")
    except Exception as e:
        logger.error(f"Failed to log privacy rejection: {e}")
