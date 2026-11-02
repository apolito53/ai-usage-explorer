"""Small GTK usage chart; GTK/Cairo are loaded only when the window is opened."""

from __future__ import annotations

import colorsys
import math
from datetime import datetime
from itertools import accumulate

from ai_usage_intraday import INTERVAL_MINUTES, bucket_costs, bucket_model_costs, cost_breakdown


ACCENT = (0.12, 0.65, 0.57)
MODEL_COLORS = (
    (0.12, 0.65, 0.57), (0.35, 0.52, 0.86), (0.90, 0.57, 0.20),
    (0.70, 0.43, 0.78), (0.86, 0.39, 0.46), (0.55, 0.66, 0.23),
)


def model_label(model):
    if not model:
        return "Unattributed"
    return model[7:] if model.startswith("claude-") else model


def cost_label(value):
    return f"${value:,.4f}" if 0 < value < 0.01 else f"${value:,.2f}"


def chart_values(daily_costs, cumulative=False):
    values = [cost for _day, cost in daily_costs]
    return list(accumulate(values)) if cumulative else values


def chart_scale(values):
    """Four evenly spaced dollar ticks, with a nonzero scale for empty data."""
    peak = max(values, default=0.0)
    target = max(peak, 0.000004) / 4 if peak else 0.01
    magnitude = 10 ** math.floor(math.log10(target))
    step = next(size * magnitude for size in (1, 2, 5, 10) if size * magnitude >= target)
    return step * 4


class ChartDependencyError(RuntimeError):
    """GTK's Cairo bridge is unavailable in the active Python environment."""


class UsageGraphWindow:
    def __init__(self, gtk, refresh, project=""):
        try:
            import gi

            gi.require_foreign("cairo")
            from gi.repository import Gdk
            import cairo
        except (ImportError, ValueError) as exc:
            raise ChartDependencyError(str(exc)) from exc

        self.Gtk, self.Gdk, self.cairo = gtk, Gdk, cairo
        self.snapshot = None
        self.cumulative = False
        self.today_cumulative = False
        self.day_cumulative = False
        self.view = "month"
        self.history_day = None
        # None follows the current month across refreshes and month rollovers.
        self.selected_month = None
        self.month_choices = ()
        self.interval_minutes = 60
        self.interval_buttons = {}
        self.history_interval_buttons = {}
        self.series = ()
        self.model_series = ()
        self.model_colors = {"": (0.55, 0.58, 0.62)}
        # None follows all models (including new ones); an empty set selects none.
        self.selected_models = None
        self.legend_models = ()
        self.model_checks = {}
        self.syncing_models = False
        self.plot_bounds = None
        self.chart_message = "Waiting for usage…"
        self.selected = None
        self.window = gtk.Window(title="Claude usage graphs")
        self.window.set_default_size(600, 430)
        self.window.set_position(gtk.WindowPosition.MOUSE)
        self.window.connect("delete-event", lambda window, _event: window.hide_on_delete())
        self.window.connect("key-press-event", self._window_key)

        body = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=12)
        body.set_border_width(20)
        self.window.add(body)
        title = gtk.Label(xalign=0)
        title.set_markup('<span size="x-large" weight="bold">Claude usage</span>')
        body.pack_start(title, False, False, 0)
        self.model_filter_label = gtk.Label(label="All models", xalign=0)
        self.model_filter_label.set_line_wrap(True)
        self.model_filter_label.set_max_width_chars(75)
        body.pack_start(self.model_filter_label, False, False, 0)
        self.subtitle = gtk.Label(label="Waiting for usage…", xalign=0)
        body.pack_start(self.subtitle, False, False, 0)
        if project:
            project_label = gtk.Label(label=f"Project: {project}", xalign=0)
            project_label.set_line_wrap(True)
            body.pack_start(project_label, False, False, 0)

        totals = gtk.Box(spacing=32)
        self.month_label = gtk.Label(xalign=0)
        self.today_label = gtk.Label(xalign=0)
        for label, caption in ((self.month_label, "Month to date"), (self.today_label, "Today")):
            label.set_markup(f'{caption}\n<span size="x-large" weight="bold">…</span>')
            totals.pack_start(label, False, False, 0)
        body.pack_start(totals, False, False, 0)

        self.ranges = gtk.Stack()
        self.ranges.set_homogeneous(False)
        switcher = gtk.StackSwitcher()
        switcher.set_stack(self.ranges)
        switcher.set_halign(gtk.Align.START)
        body.pack_start(switcher, False, False, 0)
        body.pack_start(self.ranges, False, False, 0)

        month_controls = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=8)
        navigation = gtk.Box(spacing=8)
        self.previous_month = gtk.Button(label="←")
        self.previous_month.set_tooltip_text("Previous month")
        self.previous_month.connect("clicked", self._move_month, -1)
        self.month_picker = gtk.MenuButton(label="Choose month ▾")
        self.month_popover = gtk.Popover.new(self.month_picker)
        self.month_picker.set_popover(self.month_popover)
        self.month_list = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        self.month_scroll = gtk.ScrolledWindow()
        self.month_scroll.set_policy(gtk.PolicyType.NEVER, gtk.PolicyType.AUTOMATIC)
        self.month_scroll.set_min_content_width(240)
        self.month_scroll.add(self.month_list)
        self.month_popover.add(self.month_scroll)
        self.next_month = gtk.Button(label="→")
        self.next_month.set_tooltip_text("Next month")
        self.next_month.connect("clicked", self._move_month, 1)
        navigation.pack_start(self.previous_month, False, False, 0)
        navigation.pack_start(self.month_picker, True, True, 0)
        navigation.pack_start(self.next_month, False, False, 0)
        month_controls.pack_start(navigation, False, False, 0)
        controls = gtk.Box(spacing=0)
        controls.get_style_context().add_class("linked")
        daily = gtk.RadioButton.new_with_label_from_widget(None, "Daily spending")
        cumulative = gtk.RadioButton.new_with_label_from_widget(daily, "Cumulative")
        for button, mode in ((daily, False), (cumulative, True)):
            button.set_mode(False)
            button.connect("toggled", self._select_mode, mode)
            controls.pack_start(button, False, False, 0)
        month_controls.pack_start(controls, False, False, 0)
        self.ranges.add_titled(month_controls, "month", "Monthly")

        today_controls = self._day_controls("today", self.interval_buttons)
        self.ranges.add_titled(today_controls, "today", "Today")
        self.history_controls = self._day_controls("day", self.history_interval_buttons)
        self.history_controls.show_all()
        self.history_controls.set_no_show_all(True)
        self.history_controls.hide()

        self.detail = gtk.Label(label="Waiting for usage…", xalign=0)
        self.detail.set_line_wrap(True)
        self.detail.set_max_width_chars(75)
        body.pack_start(self.detail, False, False, 0)
        self.area = gtk.DrawingArea()
        self.area.set_size_request(440, 190)
        self.area.set_can_focus(True)
        self.area.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                             | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("leave-notify-event", self._leave)
        self.area.connect("button-press-event", self._click)
        self.area.connect("key-press-event", self._chart_key)
        self.area.get_accessible().set_name("Claude spending chart")
        self.area.set_tooltip_text(
            "Hover to inspect usage, or focus the chart and use Left / Right.\n"
            "Colors show models. Click an outlined daily bar or press Enter for day details."
        )
        body.pack_start(self.area, True, True, 0)

        self.chart_note = gtk.Label(xalign=0)
        self.chart_note.set_line_wrap(True)
        self.chart_note.set_no_show_all(True)
        body.pack_start(self.chart_note, False, False, 0)
        model_controls = gtk.Box(spacing=8)
        caption = gtk.Label(label="Models · click a name to show only it", xalign=0)
        model_controls.pack_start(caption, True, True, 0)
        for caption, selection in (("All", None), ("None", set())):
            button = gtk.Button(label=caption)
            button.connect("clicked", self._select_models, selection)
            model_controls.pack_start(button, False, False, 0)
        body.pack_start(model_controls, False, False, 0)
        self.legend = gtk.FlowBox()
        self.legend.set_selection_mode(gtk.SelectionMode.NONE)
        self.legend.set_column_spacing(16)
        self.legend.set_row_spacing(4)
        self.legend.set_max_children_per_line(3)
        self.legend.set_no_show_all(True)
        body.pack_start(self.legend, False, False, 0)
        self.ranges.connect("notify::visible-child-name", self._select_view)

        footer = gtk.Box(spacing=12)
        self.updated = gtk.Label(label="Not refreshed yet", xalign=0)
        self.refresh_button = gtk.Button(label="Refresh")
        self.refresh_button.connect("clicked", refresh)
        footer.pack_start(self.updated, True, True, 0)
        footer.pack_end(self.refresh_button, False, False, 0)
        body.pack_start(footer, False, False, 0)
        self.status = gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_no_show_all(True)
        body.pack_start(self.status, False, False, 0)

    def _day_controls(self, view, buttons):
        gtk = self.Gtk
        controls = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=8)
        if view == "day":
            back = gtk.Button(label="← Back to month")
            back.set_halign(gtk.Align.START)
            back.connect("clicked", self._back_to_month)
            controls.pack_start(back, False, False, 0)
        modes = gtk.Box(spacing=0)
        modes.get_style_context().add_class("linked")
        per_interval = gtk.RadioButton.new_with_label_from_widget(None, "Per interval")
        cumulative = gtk.RadioButton.new_with_label_from_widget(per_interval, "Cumulative")
        for button, mode in ((per_interval, False), (cumulative, True)):
            button.set_mode(False)
            button.connect("toggled", self._select_mode, mode, view)
            modes.pack_start(button, False, False, 0)
        controls.pack_start(modes, False, False, 0)
        intervals = gtk.Box(spacing=0)
        intervals.get_style_context().add_class("linked")
        group = None
        for minutes in INTERVAL_MINUTES:
            button = gtk.RadioButton.new_with_label_from_widget(
                group, "1h" if minutes == 60 else f"{minutes}m",
            )
            group = group or button
            button.set_mode(False)
            button.connect("toggled", self._select_interval, minutes)
            buttons[minutes] = button
            intervals.pack_start(button, False, False, 0)
        controls.pack_start(intervals, False, False, 0)
        return controls

    @staticmethod
    def _draw_swatch(area, cr, color):
        cr.set_source_rgb(*color)
        cr.rectangle(0, (area.get_allocated_height() - 12) / 2, 12, 12)
        cr.fill()
        return False

    def present(self):
        self.window.show_all()
        self.window.present()

    def set_usage(self, snapshot):
        if self.snapshot is None or self.snapshot.refreshed_at.date() != snapshot.refreshed_at.date():
            self.selected = None
        self.snapshot = snapshot
        if self.view == "day" and self.history_day.replace(day=1) not in self._months():
            self.ranges.set_visible_child_name("month")
        self.updated.set_text(snapshot.refreshed_at.strftime("Updated %b %-d, %-I:%M %p"))
        self._update_chart()

    def _months(self):
        current = self.snapshot.refreshed_at.date().replace(day=1)
        return {current: self.snapshot.daily_costs, **self.snapshot.monthly_daily_costs}

    def _month(self):
        return self.selected_month or self.snapshot.refreshed_at.date().replace(day=1)

    def _month_days(self):
        return self._months()[self._month()]

    def _sync_month_picker(self):
        months = self._months()
        if self.selected_month is not None and self.selected_month not in months:
            self.selected_month = None
            self.selected = None
        choices = tuple(sorted(months))
        current = self.snapshot.refreshed_at.date().replace(day=1)
        # A popover opens on click and stays open until a month is chosen or dismissed.
        if choices != self.month_choices:
            for child in self.month_list.get_children():
                self.month_list.remove(child)
            for month in choices:
                label = month.strftime("%B %Y")
                button = self.Gtk.Button(label=f"This month · {label}" if month == current else label)
                button.connect("clicked", self._select_month, month)
                self.month_list.pack_start(button, False, False, 0)
            self.month_scroll.set_min_content_height(min(280, len(choices) * 40))
            self.month_scroll.show_all()
            self.month_choices = choices
        label = self._month().strftime("%B %Y")
        self.month_picker.set_label(f"This month · {label} ▾" if self._month() == current else f"{label} ▾")
        self.previous_month.set_sensitive(self._month() != choices[0])
        self.next_month.set_sensitive(self._month() != choices[-1])

    def _select_month(self, _button, month):
        if self.snapshot is None:
            return
        self.month_popover.popdown()
        current = self.snapshot.refreshed_at.date().replace(day=1)
        self.selected_month = None if month == current else month
        self.selected = None
        self._update_chart()

    def _move_month(self, _button, direction):
        if self.snapshot is None:
            return
        index = self.month_choices.index(self._month()) + direction
        if 0 <= index < len(self.month_choices):
            self._select_month(None, self.month_choices[index])

    def set_status(self, message, refreshing):
        self.status.set_text(message)
        self.status.set_visible(bool(message))
        self.refresh_button.set_sensitive(not refreshing)

    def _window_key(self, _window, event):
        if event.keyval == self.Gdk.KEY_Escape:
            if self.month_popover.get_visible():
                self.month_popover.popdown()
                self.month_picker.grab_focus()
                return True
            self.window.hide()
            return True
        return False

    def _select_mode(self, button, cumulative, view="month"):
        if button.get_active():
            if view == "today":
                self.today_cumulative = cumulative
            elif view == "day":
                self.day_cumulative = cumulative
            else:
                self.cumulative = cumulative
            self._update_chart()

    def _select_view(self, stack, _property):
        self.view = stack.get_visible_child_name()
        if self.view != "day":
            self.history_controls.hide()
            if self.history_controls.get_parent() is stack:
                stack.remove(self.history_controls)
        self.selected = None
        self._update_chart()

    def _select_interval(self, button, minutes):
        if button.get_active() and minutes != self.interval_minutes:
            self.interval_minutes = minutes
            for buttons in (self.interval_buttons, self.history_interval_buttons):
                buttons[minutes].set_active(True)
            self.selected = None
            self._update_chart()

    def _is_cumulative(self):
        if self.view == "today":
            return self.today_cumulative
        return self.day_cumulative if self.view == "day" else self.cumulative

    def _day_usage(self, day):
        if self.snapshot is None:
            return None
        if day == self.snapshot.refreshed_at.date():
            return self.snapshot.intraday
        return self.snapshot.intraday_days.get(day)

    def _has_day_details(self, day):
        usage = self._day_usage(day)
        return usage is not None and not usage.error and bool(usage.costs)

    def _values(self):
        return chart_values(self.series, self._is_cumulative())

    def _filter_parts(self, parts):
        if self.selected_models is None:
            return parts
        return {model: cost for model, cost in parts.items() if model in self.selected_models}

    def _daily_parts(self, day, total):
        return cost_breakdown(total, self.snapshot.daily_model_costs.get(day, {}))

    def _select_models(self, _button, selection):
        self.selected_models = None if selection is None else set(selection)
        self._update_chart()

    def _toggle_model(self, button, model):
        if self.syncing_models:
            return
        selection = set(self.legend_models) if self.selected_models is None else set(self.selected_models)
        if button.get_active():
            selection.add(model)
        else:
            selection.discard(model)
        self._select_models(None, None if selection == set(self.legend_models) else selection)

    def _update_header(self):
        if self.selected_models is None:
            caption = "All models"
        elif not self.selected_models:
            caption = "No models selected"
        else:
            caption = "Models: " + ", ".join(model_label(model) for model in sorted(self.selected_models))
        self.model_filter_label.set_text(caption)
        if self.snapshot is None:
            return
        current = self.snapshot.refreshed_at.date().replace(day=1)
        month_days = self._month_days()
        month = self.snapshot.month_cost if self._month() == current else sum(cost for _day, cost in month_days)
        today = self.snapshot.today_cost
        if self.selected_models is not None:
            month = sum(sum(self._filter_parts(self._daily_parts(day, total)).values())
                        for day, total in month_days)
            today = sum(self._filter_parts(self._daily_parts(self.snapshot.refreshed_at.date(), today)).values())
        month_caption = "Month to date" if self._month() == current else self._month().strftime("%B %Y total")
        for label, caption, value in ((self.month_label, month_caption, month),
                                      (self.today_label, "Today", today)):
            label.set_markup(f'{caption}\n<span size="x-large" weight="bold">${value:,.2f}</span>')

    def _update_legend(self):
        models = {model for parts in self.model_series for model in parts}
        if self.snapshot is not None:
            for series in self._months().values():
                for day, total in series:
                    models.update(self._daily_parts(day, total))
            for usage in (*self.snapshot.intraday_days.values(), self.snapshot.intraday):
                if usage is not None and not usage.error:
                    models.update(model for _time, model, cost in usage.model_costs if cost > 0)
        if self.selected_models is not None:
            models.update(self.selected_models)
        models = tuple(sorted(models))
        for model in models:
            if model not in self.model_colors:
                index = len(self.model_colors) - 1
                self.model_colors[model] = (MODEL_COLORS[index] if index < len(MODEL_COLORS)
                                            else colorsys.hsv_to_rgb((index * 0.618034) % 1, 0.6, 0.8))
        if models != self.legend_models:
            for child in self.legend.get_children():
                self.legend.remove(child)
            self.legend_models, self.model_checks = models, {}
            for model in models:
                item = self.Gtk.Box(spacing=2)
                check = self.Gtk.CheckButton()
                check.set_tooltip_text("Include " + model_label(model))
                check.get_accessible().set_name("Include " + model_label(model))
                check.connect("toggled", self._toggle_model, model)
                self.model_checks[model] = check
                item.pack_start(check, False, False, 0)
                button = self.Gtk.Button()
                button.set_relief(self.Gtk.ReliefStyle.NONE)
                button.set_tooltip_text("Show only " + model_label(model))
                button.get_accessible().set_name("Show only " + model_label(model))
                button.connect("clicked", self._select_models, {model})
                content = self.Gtk.Box(spacing=6)
                swatch = self.Gtk.DrawingArea()
                swatch.set_size_request(12, 12)
                swatch.connect("draw", self._draw_swatch, self.model_colors[model])
                content.pack_start(swatch, False, False, 0)
                label = self.Gtk.Label(label=model_label(model))
                label.set_line_wrap(True)
                label.set_max_width_chars(30)
                content.pack_start(label, False, False, 0)
                button.add(content)
                item.pack_start(button, False, False, 0)
                self.legend.add(item)
                item.show_all()
                item.get_parent().show()
        self.syncing_models = True
        try:
            for model, check in self.model_checks.items():
                check.set_active(self.selected_models is None or model in self.selected_models)
        finally:
            self.syncing_models = False
        self.legend.set_visible(bool(models))

    def _update_chart(self):
        self.series = ()
        self.model_series = ()
        note = ""
        self.chart_message = "Waiting for usage…"
        if self.snapshot is not None:
            self._sync_month_picker()
            now = self.snapshot.refreshed_at
            if self.view in ("today", "day"):
                day = now.date() if self.view == "today" else self.history_day
                is_today = day == now.date()
                heading = "Today" if is_today else day.strftime("%A")
                self.subtitle.set_text(f"{heading} · {day.strftime('%b %-d, %Y')} · USD")
                usage = self._day_usage(day)
                if usage is None:
                    self.chart_message = ("Timestamped usage not loaded yet" if is_today
                                          else "Timestamped usage unavailable for this day")
                    if self.snapshot.intraday is not None:
                        note = self.snapshot.intraday.error
                elif usage.error:
                    self.chart_message = "This day's chart is unavailable"
                    note = usage.error
                else:
                    self.series = bucket_costs(usage.costs, now, self.interval_minutes, day)
                    breakdowns = bucket_model_costs(usage.model_costs, now, self.interval_minutes, day)
                    self.model_series = tuple(cost_breakdown(total, parts)
                                              for (_time, total), parts in zip(self.series, breakdowns))
                    self.chart_message = "No priced usage available" if usage.unpriced else "No recorded spending"
                    note = "Local time"
                    if self._is_cumulative():
                        note += " · running total since midnight"
                    elif is_today:
                        note += " · current interval is incomplete"
                    else:
                        note += " · full day"
                    if usage.unpriced:
                        note += f" · {usage.unpriced} usage records could not be priced"
                    total = sum(sum(self._filter_parts(parts).values()) for parts in self.model_series)
                    daily_total = (self.snapshot.today_cost if is_today
                                   else dict(self._months().get(day.replace(day=1), ())).get(day, 0))
                    if self.selected_models is not None:
                        daily_total = sum(self._filter_parts(self._daily_parts(day, daily_total)).values())
                    if abs(total - daily_total) >= 0.01:
                        note += f"\nTimestamped total: ${total:,.2f} · daily total: ${daily_total:,.2f}"
            else:
                self.subtitle.set_text(self._month().strftime("%B %Y") + " · USD")
                self.series = self._month_days()
                self.model_series = tuple(
                    self._daily_parts(day, total)
                    for day, total in self.series
                )
                self.chart_message = "No recorded spending this month"
                if not self.cumulative:
                    note = "Colors show models · outlined bars have day details (click or press Enter)"
        if self.selected is not None and self.selected >= len(self.series):
            self.selected = None
        self.chart_note.set_text(note)
        self.chart_note.set_visible(bool(note))
        self._update_legend()
        if self.selected_models is not None:
            self.model_series = tuple(self._filter_parts(parts) for parts in self.model_series)
            self.series = tuple((stamp, sum(parts.values()))
                                for (stamp, _cost), parts in zip(self.series, self.model_series))
            if self.series:
                self.chart_message = ("No models selected" if not self.selected_models
                                      else "No spending for selected models")
        self._update_header()
        self.plot_bounds = None
        self._update_detail()
        self.area.queue_draw()

    def _update_detail(self):
        if not self.series:
            self.detail.set_text(self.chart_message)
            return
        index = self.selected if self.selected is not None else len(self.series) - 1
        day, _cost = self.series[index]
        value = self._values()[index]
        amount = cost_label(value)
        if self.view in ("today", "day"):
            end = datetime.fromtimestamp(day.timestamp() + self.interval_minutes * 60).astimezone()
            is_today = day.date() == self.snapshot.refreshed_at.date()
            last = index == len(self.series) - 1
            partial = " · so far" if is_today and last else ""
            if self._is_cumulative():
                if not is_today and last:
                    description = f"{day.strftime('%b %-d')} total · {amount}"
                else:
                    through = min(end, self.snapshot.refreshed_at.astimezone())
                    caption = "Today" if is_today else day.strftime("%b %-d")
                    description = f"{caption} through {through.strftime('%H:%M %Z')} · {amount}{partial}"
            else:
                description = f"{day.strftime('%H:%M %Z')}–{end.strftime('%H:%M %Z')} · {amount}{partial}"
        else:
            period = "Month through " if self.cumulative else ""
            partial = " · today so far" if day == self.snapshot.refreshed_at.date() else ""
            description = f"{period}{day.strftime('%b %-d')} · {amount}{partial}"
            if not self.cumulative:
                description += (" · click or press Enter for details" if self._has_day_details(day)
                                else " · daily total only")
        if not self._is_cumulative() and self.model_series:
            parts = self.model_series[index]
            if parts:
                description += "\n" + " · ".join(
                    f"{model_label(model)} {cost_label(cost)}"
                    for model, cost in sorted(parts.items())
                )
        self.detail.set_text(description)
        self.area.get_accessible().set_description(description)

    def _plot_bounds(self, width, height, context=None):
        values = self._values()
        label = self._axis_label(chart_scale(values))
        # Reserve enough room for dollar labels even with unusually large totals.
        left = max(58, context.text_extents(label)[2] + 14) if context else 58
        return left, 12, max(1, width - left - 28), max(1, height - 42)

    def _index_at(self, event):
        if not self.series or self.plot_bounds is None:
            return None
        left, top, width, height = self.plot_bounds
        if left <= event.x <= left + width and top <= event.y <= top + height:
            count = len(self.series)
            fraction = (event.x - left) / width
            index = round(fraction * (count - 1)) if self._is_cumulative() else int(fraction * count)
            return max(0, min(count - 1, index))
        return None

    @staticmethod
    def _bar_width(slot_width):
        return min(40, slot_width * (0.8 if slot_width >= 3 else 1))

    def _clickable_index(self, event):
        if self.view != "month" or self.cumulative:
            return None
        index = self._index_at(event)
        if index is None or not self._has_day_details(self.series[index][0]):
            return None
        left, top, width, height = self.plot_bounds
        slot = width / len(self.series)
        center = left + slot * (index + 0.5)
        bar_height = height * self.series[index][1] / chart_scale(self._values())
        if (abs(event.x - center) <= self._bar_width(slot) / 2
                and event.y >= top + height - max(bar_height, 3)):
            return index
        return None

    def _set_cursor(self, area, clickable):
        window = area.get_window()
        if window is not None:
            cursor = self.Gdk.Cursor.new_from_name(window.get_display(), "pointer") if clickable else None
            window.set_cursor(cursor)

    def _motion(self, area, event):
        if not self.series or self.plot_bounds is None:
            return False
        self.selected = self._index_at(event)
        self._set_cursor(area, self._clickable_index(event) is not None)
        self._update_detail()
        area.queue_draw()
        return False

    def _leave(self, area, _event):
        self.selected = None
        self._set_cursor(area, False)
        self._update_detail()
        area.queue_draw()
        return False

    def _open_day(self, index):
        if self.view != "month" or self.cumulative or index is None:
            return False
        day = self.series[index][0]
        if not self._has_day_details(day):
            return False
        if day == self.snapshot.refreshed_at.date():
            self.ranges.set_visible_child_name("today")
        else:
            self.selected_month = day.replace(day=1)
            self.history_day = day
            self.ranges.add_titled(self.history_controls, "day", day.strftime("%b %-d"))
            self.history_controls.show()
            self.ranges.set_visible_child_name("day")
        self._set_cursor(self.area, False)
        self.area.grab_focus()
        return True

    def _click(self, _area, event):
        return event.button == 1 and self._open_day(self._clickable_index(event))

    def _back_to_month(self, _button):
        self.ranges.set_visible_child_name("month")
        self.selected = next((index for index, (day, _cost) in enumerate(self.series)
                              if day == self.history_day), None)
        self._update_detail()
        self.area.grab_focus()
        self.area.queue_draw()

    def _chart_key(self, area, event):
        if not self.series:
            return False
        if event.keyval in (self.Gdk.KEY_Return, self.Gdk.KEY_KP_Enter, self.Gdk.KEY_space):
            index = self.selected if self.selected is not None else len(self.series) - 1
            return self._open_day(index)
        if event.keyval not in (self.Gdk.KEY_Left, self.Gdk.KEY_Right):
            return False
        count = len(self.series)
        index = self.selected if self.selected is not None else count - 1
        self.selected = max(0, min(count - 1, index + (1 if event.keyval == self.Gdk.KEY_Right else -1)))
        self._update_detail()
        area.queue_draw()
        return True

    @staticmethod
    def _axis_label(value):
        if value == int(value):
            return f"${value:,.0f}"
        return "$" + f"{value:,.6f}".rstrip("0")

    def _draw(self, area, cr):
        gtk = self.Gtk
        width, height = area.get_allocated_width(), area.get_allocated_height()
        style = area.get_style_context()
        gtk.render_background(style, cr, 0, 0, width, height)
        color = style.get_color(gtk.StateFlags.NORMAL)
        foreground = (color.red, color.green, color.blue)
        accent = ACCENT
        cr.set_font_size(11)
        left, top, plot_width, plot_height = self._plot_bounds(width, height, cr)
        self.plot_bounds = (left, top, plot_width, plot_height)
        bottom = top + plot_height
        values = self._values()
        ceiling = chart_scale(values)

        for tick in range(5):
            value = ceiling * tick / 4
            y = bottom - plot_height * tick / 4
            cr.set_source_rgba(*foreground, 0.12)
            cr.set_line_width(1)
            cr.move_to(left, y)
            cr.line_to(left + plot_width, y)
            cr.stroke()
            label = self._axis_label(value)
            cr.set_source_rgba(*foreground, 0.7)
            cr.move_to(left - cr.text_extents(label)[2] - 10, y + 4)
            cr.show_text(label)

        if values:
            count = len(values)
            cumulative = self._is_cumulative()
            selected = self.selected if self.selected is not None else count - 1
            slot_width = plot_width / count
            points = [
                (left + (plot_width * (index / (count - 1) if count > 1 else 0.5)
                         if cumulative else slot_width * (index + 0.5)),
                 bottom - value / ceiling * plot_height)
                for index, value in enumerate(values)
            ]
            if cumulative:
                cr.move_to(points[0][0], bottom)
                for x, y in points:
                    cr.line_to(x, y)
                cr.line_to(points[-1][0], bottom)
                cr.close_path()
                gradient = self.cairo.LinearGradient(0, top, 0, bottom)
                gradient.add_color_stop_rgba(0, *accent, 0.3)
                gradient.add_color_stop_rgba(1, *accent, 0.03)
                cr.set_source(gradient)
                cr.fill()
                cr.move_to(*points[0])
                for x, y in points[1:]:
                    cr.line_to(x, y)
                cr.set_source_rgb(*accent)
                cr.set_line_width(2.5)
                cr.set_line_join(self.cairo.LINE_JOIN_ROUND)
                cr.stroke()
            else:
                # Keep sparse bars compact and dense minute bars within their slots.
                bar_width = self._bar_width(slot_width)
                for index, (x, y) in enumerate(points):
                    base = bottom
                    for model, cost in sorted(self.model_series[index].items()):
                        segment_height = cost / ceiling * plot_height
                        cr.set_source_rgb(*self.model_colors[model])
                        cr.rectangle(x - bar_width / 2, base - segment_height, bar_width, segment_height)
                        cr.fill()
                        base -= segment_height
                    if self.view == "month" and self._has_day_details(self.series[index][0]) and values[index] > 0:
                        cr.set_source_rgba(*foreground, 0.65)
                        cr.set_line_width(1)
                        cr.rectangle(x - bar_width / 2, y, bar_width, bottom - y)
                        cr.stroke()

            x, y = points[selected]
            if self.selected is not None:
                cr.set_source_rgba(*accent, 0.5)
                cr.set_line_width(1)
                cr.set_dash([3, 3])
                cr.move_to(x, top)
                cr.line_to(x, bottom)
                cr.stroke()
                cr.set_dash([])
            if cumulative:
                cr.set_source_rgb(*accent)
                cr.arc(x, y, 4, 0, math.tau)
                cr.fill()
            # Space date labels evenly; do not crowd the axis on short months.
            ticks = sorted({round(index * (count - 1) / min(4, count - 1))
                            for index in range(min(4, count - 1) + 1)}) if count > 1 else [0]
            cr.set_source_rgba(*foreground, 0.7)
            for index in ticks:
                label = self.series[index][0].strftime("%b %-d" if self.view == "month" else "%H:%M")
                cr.move_to(points[index][0] - cr.text_extents(label)[2] / 2, bottom + 22)
                cr.show_text(label)

        if not any(values):
            message = self.chart_message
            cr.set_source_rgba(*foreground, 0.7)
            cr.move_to(left + (plot_width - cr.text_extents(message)[2]) / 2, top + plot_height / 2)
            cr.show_text(message)
        if area.has_focus():
            gtk.render_focus(style, cr, 0, 0, width, height)
        return False
