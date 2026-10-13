import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from ai_usage_intraday import (
    INTERVAL_MINUTES, ClaudeLogReader, bucket_costs, claude_log_roots,
    deduplicate_events, parse_usage_event,
)


@contextmanager
def local_timezone(name):
    try:
        with patch.dict(os.environ, {"TZ": name}):
            time.tzset()
            yield
    finally:
        time.tzset()


def record(timestamp="2026-10-09T13:05:00Z", message_id="msg", request_id="req", tokens=100, **extra):
    return {
        "timestamp": timestamp, "sessionId": "session", "requestId": request_id,
        "message": {"id": message_id, "model": "claude-test", "usage": {
            "input_tokens": tokens, "output_tokens": 10,
        }}, **extra,
    }


class IntradayTests(unittest.TestCase):
    def test_streaming_and_cross_file_duplicates_keep_complete_usage(self):
        events = [parse_usage_event(row, "fallback") for row in (
            record(tokens=10), record(tokens=100), record(tokens=10),
            record(tokens=100, sessionId="copy"),
            record(tokens=10000, request_id="replay", sessionId="copy", isSidechain=True),
        )]
        result = deduplicate_events(events)
        self.assertEqual(1, len(result))
        self.assertEqual(100, result[0].tokens[0])
        self.assertFalse(result[0].sidechain)

    def test_records_without_request_id_use_session_and_timestamp(self):
        events = [parse_usage_event(row, "fallback") for row in (
            record(request_id=None), record(request_id=None),
            record(request_id=None, timestamp="2026-10-09T13:06:00Z"),
            record(request_id=None, sessionId="separate"),
        )]
        self.assertEqual(3, len(deduplicate_events(events)))

    def test_progress_wrapper_uses_inner_timestamp_and_usage(self):
        inner = record()
        inner.pop("sessionId")
        wrapped = {"timestamp": "2026-10-10T00:00:00Z", "sessionId": "parent",
                   "data": {"message": inner}}
        event = parse_usage_event(wrapped, "fallback")
        self.assertEqual(datetime(2026, 10, 9, 13, 5, tzinfo=timezone.utc), event.timestamp)
        self.assertEqual("parent", event.session_id)
        self.assertEqual((100, 10, 0, 0), event.tokens)

    def test_invalid_or_non_usage_records_are_ignored(self):
        for row in (None, [], {}, {"message": []}, record(timestamp="broken"),
                    record(timestamp=None), record(message_id=[]), record(sessionId={}),
                    record(timestamp="2026-10-09T12:00:00"), record(tokens=-1)):
            with self.subTest(row=row):
                self.assertIsNone(parse_usage_event(row, "fallback"))

    def test_all_intervals_preserve_total_and_do_not_include_future(self):
        with local_timezone("America/New_York"):
            now = datetime(2026, 10, 9, 13, 7)
            costs = (
                (datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc), 1),
                (datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc), 2),
                (datetime(2026, 10, 9, 17, 5, tzinfo=timezone.utc), 3),
                (datetime(2026, 10, 9, 17, 8, tzinfo=timezone.utc), 100),
                (datetime(2026, 10, 9, 3, 59, tzinfo=timezone.utc), 100),
            )
            for minutes in INTERVAL_MINUTES:
                with self.subTest(minutes=minutes):
                    buckets = bucket_costs(costs, now, minutes)
                    self.assertEqual(6, sum(cost for _time, cost in buckets))
                    self.assertEqual((13 * 60 + 7) // minutes + 1, len(buckets))
                    self.assertEqual(0, buckets[0][0].hour)
            self.assertEqual(2, bucket_costs(costs, now, 5)[-2][1])
            self.assertEqual(3, bucket_costs(costs, now, 5)[-1][1])

    def test_dst_fall_back_keeps_repeated_hours_separate(self):
        with local_timezone("America/New_York"):
            now = datetime(2026, 11, 1, 7, 5, tzinfo=timezone.utc)
            costs = ((datetime(2026, 11, 1, 5, 15, tzinfo=timezone.utc), 1),
                     (datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc), 2))
            buckets = bucket_costs(costs, now, 60)
            self.assertEqual([0, 1, 2, 0], [cost for _time, cost in buckets])
            self.assertEqual(["00:00 EDT", "01:00 EDT", "01:00 EST", "02:00 EST"],
                             [stamp.strftime("%H:%M %Z") for stamp, _cost in buckets])

    def test_dst_spring_forward_has_no_fabricated_missing_hour(self):
        with local_timezone("America/New_York"):
            buckets = bucket_costs((), datetime(2026, 3, 8, 7, 5, tzinfo=timezone.utc), 60)
            self.assertEqual([0, 1, 3], [stamp.hour for stamp, _cost in buckets])

    def test_historical_days_cover_full_day_at_every_interval_including_dst(self):
        with local_timezone("America/New_York"):
            for day, hours in ((date(2026, 10, 9), 24), (date(2026, 3, 8), 23), (date(2026, 11, 1), 25)):
                start = datetime.combine(day, datetime.min.time()).astimezone()
                end = datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()
                costs = ((start, 1), (end - timedelta(seconds=1), 2), (end, 100))
                for minutes in INTERVAL_MINUTES:
                    with self.subTest(day=day, minutes=minutes):
                        buckets = bucket_costs(costs, end, minutes, day)
                        self.assertEqual(hours * 60 // minutes, len(buckets))
                        self.assertEqual(3, sum(cost for _time, cost in buckets))
                        self.assertEqual(1, buckets[0][1])
                        self.assertEqual(2, buckets[-1][1])
                        self.assertTrue(all(stamp.date() == day for stamp, _cost in buckets))
            self.assertEqual((), bucket_costs((), datetime(2026, 10, 9), 60, date(2026, 10, 10)))

    def test_reader_groups_available_local_days_and_keeps_unpriced_counts_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir, local_timezone("America/New_York"):
            root = Path(temp_dir)
            for project in ("wanted", "unwanted"):
                (root / project).mkdir()
                (root / project / "session.jsonl").write_text('\n'.join(json.dumps(row) for row in (
                    record(timestamp="2026-10-01T03:59:00Z", message_id=project + "lastmonth"),
                    record(timestamp="2026-10-01T04:00:00Z", message_id=project + "first", tokens=100),
                    record(timestamp="2026-10-09T03:59:00Z", message_id=project + "yesterday", tokens=200),
                    record(timestamp="2026-10-09T04:00:00Z", message_id=project + "today", tokens=300),
                    record(timestamp="2026-10-09T05:00:00Z", message_id=project + "unknown", tokens=400),
                    record(timestamp="2026-10-10T02:00:00Z", message_id=project + "future"),
                )))
            reader = ClaudeLogReader([root])
            now = datetime(2026, 10, 9, 20)
            price = lambda event: event.tokens[0] / 100 if event.tokens[0] < 400 else None
            days = reader.load_days(now, "wanted", price)
            self.assertEqual({date(2026, 10, day) for day in (1, 8, 9)}, set(days))
            self.assertEqual([1, 2, 3], [sum(cost for _time, cost in days[day].costs) for day in sorted(days)])
            self.assertEqual([0, 0, 1], [days[day].unpriced for day in sorted(days)])
            # Cached historical files still provide details without another read.
            with patch.object(reader, "_read_file", side_effect=AssertionError("reread")):
                self.assertEqual(days, reader.load_days(now, "wanted", price))

    def test_reader_reuses_unchanged_files_and_handles_append_truncation_and_removal(self):
        with tempfile.TemporaryDirectory() as temp_dir, local_timezone("UTC"):
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            path = project / "session.jsonl"
            path.write_text(json.dumps(record()) + '\n{"incomplete"')
            reader = ClaudeLogReader([root])
            price = lambda event: event.tokens[0] / 100
            now = datetime(2026, 10, 9, 20)
            self.assertEqual(1, len(reader.load(now, "project", price).costs))
            with patch.object(reader, "_read_file", side_effect=AssertionError("reread")):
                self.assertEqual(1, len(reader.load(now, "project", price).costs))
            with path.open("a") as handle:
                handle.write('\n' + json.dumps(record(message_id="new", tokens=200)))
            self.assertEqual(3, sum(cost for _time, cost in reader.load(now, "project", price).costs))
            path.write_text("")
            self.assertEqual((), reader.load(now, "project", price).costs)
            path.unlink()
            self.assertEqual((), reader.load(now, "project", price).costs)
            self.assertEqual({}, reader._files)

    def test_reader_filters_project_local_date_future_and_reports_missing_prices(self):
        with tempfile.TemporaryDirectory() as temp_dir, local_timezone("America/New_York"):
            root = Path(temp_dir)
            for project in ("wanted", "unwanted"):
                (root / project).mkdir()
                (root / project / "session.jsonl").write_text('\n'.join(json.dumps(row) for row in (
                    record(timestamp="2026-10-09T03:59:00Z", message_id=project + "yesterday"),
                    record(timestamp="2026-10-09T04:00:00Z", message_id=project + "today"),
                    record(timestamp="2026-10-10T02:00:00Z", message_id=project + "future"),
                )))
            reader = ClaudeLogReader([root])
            now = datetime(2026, 10, 9, 20)
            data = reader.load(now, "wanted", lambda event: 1)
            self.assertEqual(1, len(data.costs))
            self.assertEqual(1, reader.load(now, "wanted", lambda event: None).unpriced)
            self.assertEqual(2, len(reader.load(now, "", lambda event: 1).costs))
            self.assertEqual(0, len(reader.load(datetime(2026, 10, 10, 1), "", lambda event: 1).costs))

    def test_configured_roots_accept_projects_and_remove_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "projects"
            root.mkdir()
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": f"{temp_dir}, {root}"}):
                self.assertEqual([root], claude_log_roots())
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": temp_dir + "/missing"}):
                with self.assertRaises(OSError):
                    claude_log_roots()


if __name__ == "__main__":
    unittest.main()
