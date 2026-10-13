"""Read timestamped Claude usage locally, keeping only usage metadata in memory."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


INTERVAL_MINUTES = (60, 30, 15, 5, 1)
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


@dataclass(frozen=True)
class UsageEvent:
    timestamp: datetime
    model: str
    tokens: tuple
    recorded_cost: float | None
    message_id: str | None
    request_id: str | None
    session_id: str
    sidechain: bool = False
    fast: bool = False


@dataclass(frozen=True)
class IntradayUsage:
    costs: tuple = ()
    unpriced: int = 0
    error: str = ""


def claude_log_roots():
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    candidates = configured.split(",") if configured is not None else (
        str(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "claude"),
        str(Path.home() / ".claude"),
    )
    roots = []
    for value in candidates:
        if not value.strip():
            continue
        path = Path(value.strip()).expanduser().resolve()
        root = path if path.name == "projects" else path / "projects"
        if root.is_dir() and root not in roots:
            roots.append(root)
    if not roots:
        raise OSError("No local Claude projects directory found")
    return roots


def parse_usage_event(row, session_id):
    if not isinstance(row, dict):
        return None
    # Subagent progress records wrap the original timestamped message.
    if not isinstance(row.get("message"), dict):
        progress = row.get("data")
        nested = progress.get("message") if isinstance(progress, dict) else None
        if not isinstance(nested, dict):
            return None
        session_id = row.get("sessionId") or session_id
        row = nested
    message = row.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict) or message.get("model") == "<synthetic>":
        return None
    if not isinstance(row.get("timestamp"), str):
        return None
    identifiers = (message.get("id"), row.get("requestId"), row.get("sessionId"), session_id)
    if any(value is not None and not isinstance(value, str) for value in identifiers):
        return None
    try:
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        tokens = tuple(int(usage.get(key, 0) or 0) for key in TOKEN_FIELDS)
        if any(value < 0 for value in tokens):
            return None
        cost = float(row["costUSD"]) if row.get("costUSD") is not None else None
        if cost is not None and (not math.isfinite(cost) or cost < 0):
            cost = None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return UsageEvent(
        timestamp, str(message.get("model") or "unknown"), tokens, cost,
        message.get("id") or None, row.get("requestId") or None,
        row.get("sessionId") or session_id, bool(row.get("isSidechain")),
        usage.get("speed") == "fast",
    )


def deduplicate_events(events):
    """Collapse streaming/copy records, preferring complete parent usage."""
    kept, exact, routes = [], {}, {}
    for event in events:
        if event.message_id is None:
            kept.append(event)
            continue
        key = (event.message_id, event.request_id) if event.request_id else (
            event.message_id, None, event.session_id, event.timestamp,
        )
        route = (event.message_id, event.session_id)
        index = exact.get(key)
        if index is None:
            index = next((index for index in routes.get(route, [])
                          if event.sidechain or kept[index].sidechain), None)
        if index is None:
            index = len(kept)
            kept.append(event)
        else:
            old = kept[index]
            rank = lambda item: (not item.sidechain, sum(item.tokens), item.recorded_cost or 0, item.fast)
            if rank(event) > rank(old):
                kept[index] = event
        exact[key] = index
        if index not in routes.setdefault(route, []):
            routes[route].append(index)
    return kept


class ClaudeLogReader:
    """Reuse unchanged JSONL files; never write samples or conversation content."""

    def __init__(self, roots=None):
        self.roots = roots
        self._files = {}

    @staticmethod
    def _read_file(path, root):
        relative = path.relative_to(root).parts
        session_id = relative[1] if len(relative) > 2 else path.stem
        events = []
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    event = parse_usage_event(json.loads(line), session_id)
                except json.JSONDecodeError:
                    # A writer may still be appending the final line.
                    continue
                if event is not None:
                    events.append(event)
        return tuple(events)

    def load(self, now, project, price_event):
        return self.load_days(now, project, price_event).get(now.astimezone().date(), IntradayUsage())

    def load_days(self, now, project, price_event):
        """Price timestamped usage by local day for the current month."""
        roots = self.roots if self.roots is not None else claude_log_roots()
        files, events = {}, []
        for root in roots:
            for path in sorted(root.rglob("*.jsonl")):
                if project and path.relative_to(root).parts[0] != project:
                    continue
                stat = path.stat()
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                cached = self._files.get(path)
                if cached is None or cached[0] != signature:
                    cached = (signature, self._read_file(path, root))
                files[path] = cached
                events.extend(cached[1])
        self._files = files
        current = now.astimezone()
        costs, unpriced = {}, {}
        for event in deduplicate_events(events):
            day = event.timestamp.astimezone().date()
            if (day.year, day.month) != (current.year, current.month) or event.timestamp > current:
                continue
            costs.setdefault(day, [])
            cost = price_event(event)
            if cost is None or not math.isfinite(cost) or cost < 0:
                unpriced[day] = unpriced.get(day, 0) + 1
            else:
                costs[day].append((event.timestamp, cost))
        return {
            day: IntradayUsage(tuple(sorted(records)), unpriced.get(day, 0))
            for day, records in costs.items()
        }


def bucket_costs(costs, now, minutes, day=None):
    """Local-day buckets, stopping at now for today or midnight for past days."""
    if minutes not in INTERVAL_MINUTES:
        raise ValueError("Unsupported chart interval")
    # Resolve local midnight separately: its UTC offset can differ from now.
    local = now.astimezone()
    day = day or local.date()
    if day > local.date():
        return ()
    midnight = datetime.combine(day, datetime.min.time()).astimezone()
    start = midnight.timestamp()
    end = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone().timestamp()
    step = minutes * 60
    count = (int((local.timestamp() - start) // step) + 1 if day == local.date()
             else math.ceil((end - start) / step))
    totals = [0.0] * count
    for timestamp, cost in costs:
        offset = timestamp.timestamp() - start
        if 0 <= offset < end - start and timestamp.timestamp() <= local.timestamp():
            totals[int(offset // step)] += cost
    return tuple(
        (datetime.fromtimestamp(start + index * step, timezone.utc).astimezone(), cost)
        for index, cost in enumerate(totals)
    )
