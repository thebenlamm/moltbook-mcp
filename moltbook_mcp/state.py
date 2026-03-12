"""Engagement state persistence — tracks seen posts, votes, comments across sessions."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_PATH = Path.home() / ".config" / "moltbook" / "engagement-state.json"

_EMPTY_STATE = {
    "seen": {},
    "commented": {},
    "voted": {},
    "my_posts": {},
    "browsed_submolts": {},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EngagementState:
    """Lazy-loaded, atomically-saved engagement state."""

    def __init__(self) -> None:
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        if STATE_PATH.exists():
            try:
                self._data = json.loads(STATE_PATH.read_text())
                # Ensure all top-level keys exist
                for k, v in _EMPTY_STATE.items():
                    self._data.setdefault(k, v.__class__())
                return self._data
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Corrupt engagement state, backing up: {e}")
                bak = STATE_PATH.with_suffix(".json.bak")
                STATE_PATH.rename(bak)
        self._data = {k: v.copy() for k, v in _EMPTY_STATE.items()}
        return self._data

    def _save(self) -> None:
        if self._data is None:
            return
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=STATE_PATH.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── Marking methods ──────────────────────────────────────────────

    def mark_seen(self, post_id: str, cc: Optional[int] = None,
                  submolt: Optional[str] = None, author: Optional[str] = None,
                  save: bool = True) -> None:
        data = self._load()
        entry = data["seen"].get(post_id, {})
        entry["at"] = _now()
        if cc is not None:
            entry["cc"] = cc
        if submolt:
            entry["sub"] = submolt
        if author:
            entry["author"] = author
        data["seen"][post_id] = entry
        if save:
            self._save()

    def mark_commented(self, post_id: str, comment_id: Optional[str] = None) -> None:
        data = self._load()
        comments = data["commented"].setdefault(post_id, [])
        comments.append({"comment_id": comment_id, "at": _now()})
        data["commented"][post_id] = comments
        self._save()

    def mark_voted(self, target_id: str, direction: str = "up") -> None:
        data = self._load()
        data["voted"][target_id] = {"direction": direction, "at": _now()}
        self._save()

    def is_voted(self, target_id: str) -> bool:
        return target_id in self._load()["voted"]

    def get_vote_direction(self, target_id: str) -> Optional[str]:
        """Return 'up' or 'down' if voted, None otherwise."""
        entry = self._load()["voted"].get(target_id)
        if entry is None:
            return None
        # Handle migration from old format (bare timestamp string)
        if isinstance(entry, str):
            return "up"
        return entry.get("direction", "up")

    def mark_my_post(self, post_id: str) -> None:
        data = self._load()
        data["my_posts"][post_id] = _now()
        self._save()

    def mark_browsed_submolt(self, name: str) -> None:
        data = self._load()
        data["browsed_submolts"][name] = _now()
        self._save()

    def prune_seen(self, post_id: str) -> None:
        data = self._load()
        data["seen"].pop(post_id, None)
        self._save()

    # ── Query methods ────────────────────────────────────────────────

    def get_annotations(self, post_id: str) -> dict[str, Any]:
        data = self._load()
        ann: dict[str, Any] = {}
        if post_id in data["commented"]:
            ann["commented"] = len(data["commented"][post_id])
        vote_dir = self.get_vote_direction(post_id)
        if vote_dir:
            ann["voted"] = vote_dir
        if post_id in data["my_posts"]:
            ann["my_post"] = True
        return ann

    def get_thread_diff_candidates(self, scope: str = "engaged") -> list[dict]:
        """Return post IDs + stored comment counts for thread diffing.

        scope:
          - "engaged": posts we've commented on or created
          - "all": all seen posts with a stored comment count
        """
        data = self._load()
        candidates = []

        if scope == "all":
            for pid, info in data["seen"].items():
                if "cc" in info:
                    candidates.append({"post_id": pid, "cc": info["cc"], "at": info["at"]})
        else:
            # Posts we've commented on or created
            engaged_ids = set(data["commented"].keys()) | set(data["my_posts"].keys())
            for pid in engaged_ids:
                info = data["seen"].get(pid, {})
                candidates.append({
                    "post_id": pid,
                    "cc": info.get("cc", 0),
                    "at": info.get("at", ""),
                })

        # Most recent first
        candidates.sort(key=lambda c: c["at"], reverse=True)
        return candidates

    def digest(self, fmt: str = "compact") -> str:
        data = self._load()
        seen = len(data["seen"])
        commented = len(data["commented"])
        voted = len(data["voted"])
        my_posts = len(data["my_posts"])
        submolts = len(data["browsed_submolts"])

        if fmt == "compact":
            return (f"Seen: {seen} | Commented: {commented} | "
                    f"Voted: {voted} | My posts: {my_posts} | Submolts: {submolts}")

        lines = [
            "Engagement State",
            f"  Posts seen: {seen}",
            f"  Posts commented on: {commented}",
            f"  Votes cast: {voted}",
            f"  Own posts: {my_posts}",
            f"  Submolts browsed: {submolts}",
        ]
        if data["browsed_submolts"]:
            lines.append(f"  Submolt list: {', '.join(sorted(data['browsed_submolts'].keys()))}")
        if data["my_posts"]:
            lines.append(f"  Own post IDs: {', '.join(list(data['my_posts'].keys())[:10])}")
        return "\n".join(lines)


state = EngagementState()
