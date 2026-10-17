"""Internal tools — local DB queries for agent context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from talos_agent.tools.registry import tool

if TYPE_CHECKING:
    from talos_agent.config import Settings
    from talos_agent.db import LocalDB

# Injected by registry.build_all_tools
_db: LocalDB = None  # type: ignore[assignment]
_settings: Settings = None  # type: ignore[assignment]


def _content_history_max_limit() -> int:
    """Resolve the configured hard cap, falling back to a safe default."""
    if _settings is not None:
        try:
            return int(_settings.content_history_max_limit)
        except (AttributeError, TypeError, ValueError):
            pass
    return 50


def _research_notes_max_bytes() -> int:
    """Resolve the configured note size budget, falling back to a safe default."""
    if _settings is not None:
        try:
            return int(_settings.research_notes_max_bytes)
        except (AttributeError, TypeError, ValueError):
            pass
    return 4096


def _clamp_limit(limit: int, max_limit: int) -> int:
    """Clamp an LLM-provided limit to [1, max_limit]."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(limit, 1)
    limit = min(limit, max_limit)
    return limit


def _truncate_notes(notes: str, max_bytes: int) -> str:
    """Truncate notes to max_bytes (UTF-8), recording when truncation occurs."""
    if not isinstance(notes, str):
        notes = str(notes) if notes is not None else ""
    encoded = notes.encode("utf-8")
    if len(encoded) <= max_bytes:
        return notes
    # Truncate at a UTF-8 boundary so we never emit a partial codepoint.
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    marker = "\n[truncated: note exceeded size budget]"
    # Make sure the marker fits inside the budget after truncation.
    if len(truncated.encode("utf-8")) + len(marker.encode("utf-8")) > max_bytes:
        # Shrink truncated so marker fits.
        budget = max_bytes - len(marker.encode("utf-8"))
        if budget < 0:
            return marker
        truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + marker


@tool("get_content_history", "Get recent content history to avoid duplicate posts")
async def get_content_history(limit: int = 10) -> dict:
    max_limit = _content_history_max_limit()
    clamped = _clamp_limit(limit, max_limit)
    rows = _db.get_recent_content(clamped)
    return {
        "history": rows,
        "limit_requested": limit,
        "limit_applied": clamped,
        "limit_clamped": limit != clamped,
        "max_limit": max_limit,
    }


@tool("get_schedule_status", "Get today's activity counts and last run times")
async def get_schedule_status() -> dict:
    return {
        "posts_today": _db.count_today("post"),
        "research_today": _db.count_today("research"),
        "replies_today": _db.count_today("reply"),
        "last_agent_cycle": str(_db.get_last_run("agent_cycle") or "never"),
    }


@tool("save_research_notes", "Save research findings to local DB for future reference")
async def save_research_notes(notes: str, channel: str = "research") -> dict:
    max_bytes = _research_notes_max_bytes()
    original_len = len(notes.encode("utf-8")) if isinstance(notes, str) else 0
    truncated = _truncate_notes(notes, max_bytes)
    was_truncated = len(truncated.encode("utf-8")) != original_len or truncated != notes
    _db.add_activity("research", truncated, channel)
    return {
        "status": "saved",
        "truncated": was_truncated,
        "original_bytes": original_len,
        "stored_bytes": len(truncated.encode("utf-8")),
        "max_bytes": max_bytes,
    }


@tool("get_active_playbook", "Get the currently active GTM playbook strategy")
async def get_active_playbook() -> dict:
    playbook = _db.get_active_playbook()
    if playbook:
        return playbook
    return {"status": "no_active_playbook"}


@tool("record_post", "Record a posted content to prevent duplicates")
async def record_post(content: str, channel: str) -> dict:
    _db.add_content(content, channel)
    _db.add_activity("post", content, channel)
    return {"status": "recorded"}
