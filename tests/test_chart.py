from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ai_usage_chart import UsageGraphWindow, chart_scale, chart_values
from ai_usage_intraday import INTERVAL_MINUTES, IntradayUsage, bucket_costs


class ChartDataTests(unittest.TestCase):
    def make_graph(self):
        graph = UsageGraphWindow.__new__(UsageGraphWindow)
        graph.snapshot = SimpleNamespace(
            refreshed_at=datetime(2026, 10, 10, 2, 7), month_cost=10, today_cost=2,
            daily_costs=tuple((date(2026, 10, day), cost) for day, cost in ((8, 5), (9, 3), (10, 2))),
            intraday=IntradayUsage(((datetime(2026, 10, 10, 1, 10).astimezone(), 2),)),
            intraday_days={date(2026, 10, 8): IntradayUsage((
                (datetime(2026, 10, 8, 0, 10).astimezone(), 2),
                (datetime(2026, 10, 8, 1, 10).astimezone(), 3),
            ))},
            daily_model_costs={date(2026, 10, 8): {"claude-opus-5": 3, "claude-sonnet-4": 2}},
        )
        graph.model_colors = {"": (0.55, 0.58, 0.62)}
        graph.selected_models = None
        graph.legend_models = ()
        graph.model_checks = {}
        graph.syncing_models = False
        graph.Gtk = Mock()
        graph.view, graph.history_day, graph.selected = "month", None, None
        graph.cumulative = graph.today_cumulative = graph.day_cumulative = False
        graph.interval_minutes = 60
        graph.Gdk = SimpleNamespace(KEY_Return=13, KEY_KP_Enter=14, KEY_space=32, KEY_Left=37, KEY_Right=39)
        for name in ("detail", "area", "subtitle", "chart_note", "legend", "history_controls", "ranges",
                     "month_label", "today_label", "updated", "_set_cursor", "model_filter_label"):
            setattr(graph, name, Mock())
        graph.legend.get_children.return_value = []
        graph.ranges.set_visible_child_name.side_effect = lambda view: graph._select_view(
            SimpleNamespace(get_visible_child_name=lambda: view), None,
        )
        graph._update_chart()
        graph.plot_bounds = (0, 0, 300, 100)
        return graph

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

    def test_click_opens_only_available_daily_bars_and_back_restores_selection(self):
        graph = self.make_graph()
        graph.legend.set_visible.assert_called_with(True)
        for x, y, button in ((150, 90, 1), (5, 90, 1), (50, 5, 1), (50, 105, 1), (50, 90, 3)):
            self.assertFalse(graph._click(graph.area, SimpleNamespace(x=x, y=y, button=button)))
            self.assertEqual("month", graph.view)
        self.assertTrue(graph._click(graph.area, SimpleNamespace(x=50, y=90, button=1)))
        self.assertEqual("day", graph.view)
        self.assertEqual(date(2026, 10, 8), graph.history_day)
        graph.legend.set_visible.assert_called_with(True)
        self.assertEqual(24, len(graph.series))
        self.assertEqual(5, sum(cost for _time, cost in graph.series))
        self.assertNotIn("so far", graph.detail.set_text.call_args.args[0])
        graph._back_to_month(None)
        self.assertEqual(("month", 0), (graph.view, graph.selected))
        graph.history_controls.hide.assert_called()

    def test_keyboard_opens_history_or_today_and_cumulative_month_cannot_open(self):
        graph = self.make_graph()
        graph.selected = 0
        graph.cumulative = True
        self.assertFalse(graph._chart_key(graph.area, SimpleNamespace(keyval=13)))
        graph.cumulative = False
        self.assertTrue(graph._chart_key(graph.area, SimpleNamespace(keyval=13)))
        self.assertEqual("day", graph.view)
        graph._select_mode(Mock(get_active=lambda: True), True, "day")
        self.assertEqual(5, graph._values()[-1])
        graph.detail.set_text.assert_called_with("Oct 8 total · $5.00")
        self.assertFalse(graph.today_cumulative)
        graph._back_to_month(None)
        graph.selected = 2
        self.assertTrue(graph._chart_key(graph.area, SimpleNamespace(keyval=32)))
        self.assertEqual("today", graph.view)
        self.assertEqual(3, len(graph.series))
        self.assertIn("so far", graph.detail.set_text.call_args.args[0])

    def test_history_refresh_handles_partial_missing_and_new_month_data(self):
        graph = self.make_graph()
        graph._open_day(0)
        graph.snapshot.intraday_days[graph.history_day] = IntradayUsage((
            (datetime(2026, 10, 8, 0, 10).astimezone(), 1),
        ), unpriced=2)
        graph.set_usage(graph.snapshot)
        self.assertEqual("day", graph.view)
        note = graph.chart_note.set_text.call_args.args[0]
        self.assertIn("2 usage records could not be priced", note)
        self.assertIn("Timestamped total: $1.00 · daily total: $5.00", note)
        graph.snapshot.intraday_days.clear()
        graph.snapshot.intraday = IntradayUsage(error="Logs unavailable")
        graph.set_usage(graph.snapshot)
        self.assertEqual((), graph.series)
        graph.chart_note.set_text.assert_called_with("Logs unavailable")
        graph.snapshot.refreshed_at = datetime(2026, 11, 1)
        graph.set_usage(graph.snapshot)
        self.assertEqual("month", graph.view)
        self.assertFalse(graph._open_day(0))

    def test_scale_handles_empty_single_day_and_large_spend(self):
        for values in ([], [0], [0.00001], [0.0015], [0.01], [1.5], [132], [12980.22]):
            with self.subTest(values=values):
                scale = chart_scale(values)
                self.assertGreater(scale, 0)
                self.assertGreaterEqual(scale, max(values, default=0))
                ticks = [UsageGraphWindow._axis_label(scale * index / 4) for index in range(5)]
                self.assertEqual(5, len(set(ticks)))

    def test_model_stacks_preserve_totals_hover_details_and_colors_across_views(self):
        graph = self.make_graph()
        graph.selected = 0
        graph._update_detail()
        self.assertIn("opus-5 $3.00 · sonnet-4 $2.00", graph.detail.set_text.call_args.args[0])
        colors = dict(graph.model_colors)
        self.assertEqual([5, 3, 2], [sum(parts.values()) for parts in graph.model_series])
        self.assertEqual({"": 3}, graph.model_series[1])
        day = date(2026, 10, 8)
        costs = graph.snapshot.intraday_days[day].costs
        graph.snapshot.intraday_days[day] = IntradayUsage(costs, model_costs=(
            (costs[0][0], "claude-sonnet-4", 2), (costs[1][0], "claude-opus-5", 3),
        ))
        graph._open_day(0)
        self.assertEqual({"claude-sonnet-4": 2}, graph.model_series[0])
        self.assertEqual({"claude-opus-5": 3}, graph.model_series[1])
        self.assertEqual(colors, graph.model_colors)
        graph._select_mode(Mock(get_active=lambda: True), True, "day")
        graph.legend.set_visible.assert_called_with(True)
        self.assertEqual(5, graph._values()[-1])

    def test_model_selection_filters_stacks_cumulative_header_and_empty_state(self):
        graph = self.make_graph()
        original = graph.snapshot.daily_costs
        graph._select_models(None, {"claude-opus-5"})
        self.assertEqual([3, 0, 0], graph._values())
        graph.model_filter_label.set_text.assert_called_with("Models: opus-5")
        self.assertIn("$3.00", graph.month_label.set_markup.call_args.args[0])
        self.assertIn("$0.00", graph.today_label.set_markup.call_args.args[0])
        self.assertEqual({"", "claude-opus-5", "claude-sonnet-4"}, set(graph.legend_models))
        graph.selected = 0
        graph._update_detail()
        self.assertIn("opus-5 $3.00", graph.detail.set_text.call_args.args[0])
        self.assertNotIn("sonnet", graph.detail.set_text.call_args.args[0])
        graph._toggle_model(Mock(get_active=lambda: True), "claude-sonnet-4")
        self.assertEqual([5, 0, 0], graph._values())
        graph._select_mode(Mock(get_active=lambda: True), True)
        self.assertEqual([5, 5, 5], graph._values())
        graph.legend.set_visible.assert_called_with(True)
        graph._select_models(None, set())
        self.assertEqual([0, 0, 0], graph._values())
        self.assertEqual("No models selected", graph.chart_message)
        graph.model_filter_label.set_text.assert_called_with("No models selected")
        self.assertIn("$0.00", graph.month_label.set_markup.call_args.args[0])
        graph._select_models(None, None)
        self.assertEqual([5, 8, 10], graph._values())
        self.assertEqual(original, graph.snapshot.daily_costs)
        self.assertIn("$10.00", graph.month_label.set_markup.call_args.args[0])
        self.assertIn("$2.00", graph.today_label.set_markup.call_args.args[0])

    def test_filter_persists_across_day_views_intervals_and_refresh(self):
        graph = self.make_graph()
        opus, sonnet = "claude-opus-5", "claude-sonnet-4"
        for day, usage in list(graph.snapshot.intraday_days.items()) + [
            (graph.snapshot.refreshed_at.date(), graph.snapshot.intraday)
        ]:
            models = tuple((stamp, opus if i == 0 else sonnet, cost)
                           for i, (stamp, cost) in enumerate(usage.costs))
            replacement = IntradayUsage(usage.costs, model_costs=models)
            if day == graph.snapshot.refreshed_at.date():
                graph.snapshot.intraday = replacement
                graph.snapshot.daily_model_costs[day] = {opus: 2}
            else:
                graph.snapshot.intraday_days[day] = replacement
        graph._select_models(None, {opus})
        self.assertIn("$5.00", graph.month_label.set_markup.call_args.args[0])
        self.assertIn("$2.00", graph.today_label.set_markup.call_args.args[0])
        graph._open_day(0)
        self.assertEqual("day", graph.view)
        self.assertEqual(2, sum(graph._values()))
        note = graph.chart_note.set_text.call_args.args[0]
        self.assertIn("Timestamped total: $2.00 · daily total: $3.00", note)
        for minutes in INTERVAL_MINUTES:
            graph.interval_minutes = minutes
            graph._update_chart()
            self.assertEqual(2, sum(graph._values()))
        graph._back_to_month(None)
        graph._open_day(2)
        self.assertEqual("today", graph.view)
        graph._select_mode(Mock(get_active=lambda: True), True, "today")
        self.assertEqual(2, graph._values()[-1])
        graph.set_usage(graph.snapshot)
        self.assertEqual({opus}, graph.selected_models)
        self.assertEqual(2, graph._values()[-1])

    def test_refresh_keeps_custom_selection_and_all_includes_new_models(self):
        graph = self.make_graph()
        graph._select_models(None, {"claude-opus-5"})
        graph.snapshot.daily_model_costs[date(2026, 10, 9)] = {"claude-haiku": 3}
        graph.set_usage(graph.snapshot)
        self.assertEqual([3, 0, 0], graph._values())
        self.assertIn("claude-haiku", graph.legend_models)
        graph._select_models(None, None)
        graph.snapshot.daily_model_costs[date(2026, 10, 10)] = {"claude-new": 2}
        graph.set_usage(graph.snapshot)
        self.assertIsNone(graph.selected_models)
        self.assertEqual([5, 3, 2], graph._values())
        self.assertIn("claude-new", graph.legend_models)
        graph._toggle_model(Mock(get_active=lambda: False), "claude-new")
        self.assertEqual([5, 3, 0], graph._values())
        graph._toggle_model(Mock(get_active=lambda: True), "claude-new")
        self.assertIsNone(graph.selected_models)

    def test_no_usage_for_selected_model_keeps_filters_available(self):
        graph = self.make_graph()
        graph._select_models(None, {"claude-opus-5"})
        graph._open_day(2)
        self.assertEqual([0, 0, 0], graph._values())
        self.assertEqual("No spending for selected models", graph.chart_message)
        self.assertIn("claude-opus-5", graph.legend_models)
        graph.legend.set_visible.assert_called_with(True)
        graph._select_models(None, {""})
        self.assertEqual(2, sum(graph._values()))
        graph.model_filter_label.set_text.assert_called_with("Models: Unattributed")


if __name__ == "__main__":
    unittest.main()
