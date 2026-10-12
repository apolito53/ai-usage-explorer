from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ai_usage_chart import UsageGraphWindow, chart_scale, chart_values
from ai_usage_intraday import INTERVAL_MINUTES, bucket_costs


class ChartDataTests(unittest.TestCase):
    def test_pointer_events_before_first_draw_are_ignored(self):
        graph = UsageGraphWindow.__new__(UsageGraphWindow)
        graph.series = [(date(2026, 10, 9), 1.0)]
        graph.plot_bounds = None
        self.assertFalse(graph._motion(None, None))

    def test_daily_and_cumulative_preserve_idle_days(self):
        days = [(date(2026, 10, day), cost) for day, cost in enumerate([2.0, 0, 3.5], 1)]
        self.assertEqual([2.0, 0, 3.5], chart_values(days))
        self.assertEqual([2.0, 2.0, 5.5], chart_values(days, cumulative=True))
        self.assertEqual([], chart_values([], cumulative=True))

    def test_today_cumulative_preserves_total_at_every_interval_and_stops_at_refresh_time(self):
        now = datetime(2026, 10, 12, 2, 7)
        costs = ((datetime(2026, 10, 12, 0, 10).astimezone(), 2),
                 (datetime(2026, 10, 12, 1, 10).astimezone(), 3))
        graph = UsageGraphWindow.__new__(UsageGraphWindow)
        graph.view, graph.today_cumulative, graph.cumulative = "today", True, False
        graph.snapshot = SimpleNamespace(refreshed_at=now)
        graph.selected, graph.detail, graph.area = None, Mock(), Mock()
        for minutes in INTERVAL_MINUTES:
            with self.subTest(minutes=minutes):
                graph.interval_minutes = minutes
                graph.series = bucket_costs(costs, now, minutes)
                values = graph._values()
                self.assertEqual(5, values[-1])
                self.assertEqual(sorted(values), values)
                self.assertEqual(values[-2], values[-1])
                graph._update_detail()
                graph.detail.set_text.assert_called_with(
                    f"Today through {now.astimezone().strftime('%H:%M %Z')} · $5.00 · so far"
                )

    def test_today_mode_is_independent_of_month_mode(self):
        graph = UsageGraphWindow.__new__(UsageGraphWindow)
        graph.cumulative, graph.today_cumulative = True, False
        graph.series = [(date(2026, 10, day), cost) for day, cost in enumerate([2, 0, 3], 1)]
        graph._update_chart = Mock()
        button = Mock(get_active=Mock(return_value=True))
        graph._select_mode(button, True, "today")
        graph._select_mode(button, False, "month")
        graph.view = "today"
        self.assertEqual([2, 2, 5], graph._values())
        graph.view = "month"
        self.assertEqual([2, 0, 3], graph._values())

    def test_scale_handles_empty_single_day_and_large_spend(self):
        for values in ([], [0], [0.00001], [0.0015], [0.01], [1.5], [132], [12980.22]):
            with self.subTest(values=values):
                scale = chart_scale(values)
                self.assertGreater(scale, 0)
                self.assertGreaterEqual(scale, max(values, default=0))
                ticks = [UsageGraphWindow._axis_label(scale * index / 4) for index in range(5)]
                self.assertEqual(5, len(set(ticks)))


if __name__ == "__main__":
    unittest.main()
