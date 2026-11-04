from __future__ import annotations

import http.client
import json
import os
import time
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from ai_usage_intraday import IntradayUsage
from ai_usage_web import UsageGraphServer, day_payload, snapshot_payload


def sample_snapshot():
    now = datetime(2024, 3, 2, 10, 30)
    stamp = datetime(2024, 3, 2, 9, 15).astimezone()
    past = datetime(2024, 2, 29, 23, 30).astimezone()
    usage = IntradayUsage(costs=((stamp, 3.0),),
                         model_costs=((stamp, "sonnet", 2.0),))
    return SimpleNamespace(
        refreshed_at=now, month_cost=8.0, today_cost=3.0,
        daily_costs=((date(2024, 3, 1), 5.0), (date(2024, 3, 2), 3.0)),
        monthly_daily_costs={
            date(2024, 2, 1): ((date(2024, 2, 29), 12.0),),
            date(2024, 3, 1): ((date(2024, 3, 1), 5.0), (date(2024, 3, 2), 3.0)),
        },
        daily_model_costs={date(2024, 3, 2): {"sonnet": 2.0}},
        intraday=usage, intraday_days={date(2024, 3, 2): usage,
            date(2024, 2, 29): IntradayUsage(costs=((past, 12.0),))},
        ccusage_path="/private/executable", session_contents="must never be serialized",
    )


class BrowserChartDataTests(unittest.TestCase):
    def test_months_keep_totals_attribution_and_available_day_details(self):
        data = snapshot_payload(sample_snapshot(), "Project <test>")
        self.assertEqual(["2024-02", "2024-03"], list(data["months"]))
        self.assertEqual(8.0, data["monthTotal"])
        self.assertEqual(3.0, data["todayTotal"])
        self.assertEqual({"sonnet": 2.0, "": 1.0}, data["months"]["2024-03"][-1]["models"])
        self.assertTrue(data["months"]["2024-02"][0]["details"])
        self.assertFalse(data["months"]["2024-03"][0]["details"])
        self.assertEqual(["", "sonnet"], data["models"])
        encoded = json.dumps(data)
        self.assertNotIn("session_contents", encoded)
        self.assertNotIn("/private/executable", encoded)

    def test_intraday_uses_shared_buckets_including_unattributed_cost(self):
        for interval, count in ((60, 11), (30, 22), (15, 43), (5, 127), (1, 631)):
            with self.subTest(interval=interval):
                data = day_payload(sample_snapshot(), date(2024, 3, 2), interval)
                self.assertEqual(count, len(data["series"]))
                self.assertEqual(3, sum(row["total"] for row in data["series"]))
                self.assertEqual(3, sum(sum(row["models"].values()) for row in data["series"]))
                self.assertIn("10:30", data["series"][-1]["range"])
        past = day_payload(sample_snapshot(), date(2024, 2, 29), 60)
        self.assertEqual(24, len(past["series"]))
        self.assertEqual(12, past["series"][-1]["total"])

    def test_missing_logs_are_explicit_and_daily_totals_remain_available(self):
        snapshot = sample_snapshot()
        snapshot.intraday = IntradayUsage(error="No local Claude projects directory found")
        snapshot.intraday_days = {}
        payload = snapshot_payload(snapshot)
        self.assertEqual(8, payload["monthTotal"])
        self.assertFalse(payload["months"]["2024-03"][-1]["details"])
        data = day_payload(snapshot, date(2024, 2, 29), 15)
        self.assertEqual([], data["series"])
        self.assertIn("No local Claude", data["error"])

    def test_invalid_day_and_interval_are_rejected(self):
        for day, interval in ((date(2024, 3, 3), 60), (date(2024, 3, 1), 0)):
            with self.assertRaises(ValueError):
                day_payload(sample_snapshot(), day, interval)

    @unittest.skipUnless(hasattr(time, "tzset"), "requires tzset")
    def test_dst_days_keep_23_or_25_hours_and_disambiguate_repeated_times(self):
        self.addCleanup(time.tzset)
        with patch.dict(os.environ, {"TZ": "America/New_York"}):
            time.tzset()
            for day, now, count in ((date(2026, 3, 8), datetime(2026, 3, 9), 23),
                                    (date(2026, 11, 1), datetime(2026, 11, 2), 25)):
                snapshot = sample_snapshot()
                snapshot.refreshed_at = now
                snapshot.intraday_days = {day: IntradayUsage()}
                rows = day_payload(snapshot, day, 60)["series"]
                self.assertEqual(count, len(rows))
                if count == 25:
                    repeated = [row["range"] for row in rows if row["label"] == "01:00"]
                    self.assertEqual(2, len(repeated))
                    self.assertTrue(repeated[0].startswith("01:00 EDT"))
                    self.assertTrue(repeated[1].startswith("01:00 EST"))


class BrowserChartServerTests(unittest.TestCase):
    def setUp(self):
        self.refresh = Mock()
        self.graphs = UsageGraphServer(self.refresh)
        self.url = self.graphs.start()
        self.addCleanup(self.graphs.close)
        self.address = urlsplit(self.url)

    def request(self, path, method="GET", headers=None, absolute=False):
        connection = http.client.HTTPConnection(self.address.hostname, self.address.port, timeout=5)
        try:
            connection.request(method, path if absolute else self.address.path + path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_on_demand_server_reuses_url_and_serves_only_bundled_assets(self):
        self.assertEqual("127.0.0.1", self.address.hostname)
        self.assertEqual(self.url, self.graphs.start())
        for asset in ("", "chart.js", "chart.css"):
            status, headers, body = self.request(asset)
            self.assertEqual(200, status)
            self.assertTrue(body)
            self.assertEqual("no-store", headers["Cache-Control"])
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(404, self.request("../../ai-usage-tray.py")[0])
        self.assertEqual(404, self.request("unknown")[0])

    def test_refresh_errors_keep_last_successful_snapshot(self):
        initial = json.loads(self.request("data")[2])
        self.assertIsNone(initial["usage"])
        self.graphs.set_usage(sample_snapshot())
        self.graphs.set_status("Refreshing…", True)
        refreshing = json.loads(self.request("data")[2])
        self.assertTrue(refreshing["refreshing"])
        self.graphs.set_status("Refresh failed: offline")
        failed = json.loads(self.request("data")[2])
        self.assertEqual(refreshing["usage"], failed["usage"])
        self.assertFalse(failed["refreshing"])
        self.assertIn("offline", failed["status"])

    def test_host_origin_and_private_path_are_checked(self):
        self.assertEqual(403, self.request("/data", absolute=True)[0])
        self.assertEqual(403, self.request("data", headers={"Host": "example.com"})[0])
        self.assertEqual(403, self.request("data", headers={"Origin": "https://example.com"})[0])
        self.assertEqual(403, self.request("refresh", method="POST")[0])
        self.refresh.assert_not_called()
        status, _headers, _body = self.request("refresh", method="POST", headers={
            "Origin": self.graphs.origin, "Content-Type": "application/json",
        })
        self.assertEqual(202, status)
        self.refresh.assert_called_once_with()

    def test_day_api_validation_and_error_recovery(self):
        self.graphs.set_usage(sample_snapshot())
        status, _headers, data = self.request("day?date=2024-03-02&interval=30")
        self.assertEqual(200, status)
        self.assertEqual(22, len(json.loads(data)["series"]))
        for path in ("day", "day?date=bad&interval=30", "day?date=2024-03-02&interval=3"):
            self.assertEqual(400, self.request(path)[0])
        self.assertEqual(200, self.request("data")[0])


if __name__ == "__main__":
    unittest.main()
