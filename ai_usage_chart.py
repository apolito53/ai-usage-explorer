"""Small GTK usage chart; GTK/Cairo are loaded only when the window is opened."""

from __future__ import annotations

import math
from datetime import datetime
from itertools import accumulate

from ai_usage_intraday import INTERVAL_MINUTES, bucket_costs


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


class UsageGraphWindow:
    def __init__(self, gtk, refresh, project=""):
        import gi

        gi.require_foreign("cairo")
        from gi.repository import Gdk
        import cairo

        self.Gtk, self.Gdk, self.cairo = gtk, Gdk, cairo
        self.snapshot = None
        self.cumulative = False
        self.today_cumulative = False
        self.view = "month"
        self.interval_minutes = 60
        self.series = ()
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

        controls = gtk.Box(spacing=0)
        controls.get_style_context().add_class("linked")
        daily = gtk.RadioButton.new_with_label_from_widget(None, "Daily spending")
        cumulative = gtk.RadioButton.new_with_label_from_widget(daily, "Cumulative")
        for button, mode in ((daily, False), (cumulative, True)):
            button.set_mode(False)
            button.connect("toggled", self._select_mode, mode)
            controls.pack_start(button, False, False, 0)
        self.ranges.add_titled(controls, "month", "This month")

        today_controls = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=8)
        today_modes = gtk.Box(spacing=0)
        today_modes.get_style_context().add_class("linked")
        per_interval = gtk.RadioButton.new_with_label_from_widget(None, "Per interval")
        today_cumulative = gtk.RadioButton.new_with_label_from_widget(per_interval, "Cumulative")
        for button, mode in ((per_interval, False), (today_cumulative, True)):
            button.set_mode(False)
            button.connect("toggled", self._select_mode, mode, "today")
            today_modes.pack_start(button, False, False, 0)
        today_controls.pack_start(today_modes, False, False, 0)

        intervals = gtk.Box(spacing=0)
        intervals.get_style_context().add_class("linked")
        group = None
        self.interval_buttons = {}
        for minutes in INTERVAL_MINUTES:
            button = gtk.RadioButton.new_with_label_from_widget(
                group, "1h" if minutes == 60 else f"{minutes}m",
            )
            group = group or button
            button.set_mode(False)
            button.connect("toggled", self._select_interval, minutes)
            self.interval_buttons[minutes] = button
            intervals.pack_start(button, False, False, 0)
        today_controls.pack_start(intervals, False, False, 0)
        self.ranges.add_titled(today_controls, "today", "Today")

        self.detail = gtk.Label(label="Waiting for usage…", xalign=0)
        body.pack_start(self.detail, False, False, 0)
        self.area = gtk.DrawingArea()
        self.area.set_size_request(440, 190)
        self.area.set_can_focus(True)
        self.area.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("leave-notify-event", self._leave)
        self.area.connect("key-press-event", self._chart_key)
        self.area.get_accessible().set_name("Claude spending chart")
        self.area.set_tooltip_text("Hover to inspect usage, or focus the chart and use Left / Right.")
        body.pack_start(self.area, True, True, 0)

        self.chart_note = gtk.Label(xalign=0)
        self.chart_note.set_line_wrap(True)
        self.chart_note.set_no_show_all(True)
        body.pack_start(self.chart_note, False, False, 0)
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

    def present(self):
        self.window.show_all()
        self.window.present()

    def set_usage(self, snapshot):
        if self.snapshot is None or self.snapshot.refreshed_at.date() != snapshot.refreshed_at.date():
            self.selected = None
        self.snapshot = snapshot
        for label, caption, value in (
            (self.month_label, "Month to date", snapshot.month_cost),
            (self.today_label, "Today", snapshot.today_cost),
        ):
            label.set_markup(f'{caption}\n<span size="x-large" weight="bold">${value:,.2f}</span>')
        self.updated.set_text(snapshot.refreshed_at.strftime("Updated %b %-d, %-I:%M %p"))
        self._update_chart()

    def set_status(self, message, refreshing):
        self.status.set_text(message)
        self.status.set_visible(bool(message))
        self.refresh_button.set_sensitive(not refreshing)

    def _window_key(self, _window, event):
        if event.keyval == self.Gdk.KEY_Escape:
            self.window.hide()
            return True
        return False

    def _select_mode(self, button, cumulative, view="month"):
        if button.get_active():
            if view == "today":
                self.today_cumulative = cumulative
            else:
                self.cumulative = cumulative
            self._update_chart()

    def _select_view(self, stack, _property):
        self.view = stack.get_visible_child_name()
        self.selected = None
        self._update_chart()

    def _select_interval(self, button, minutes):
        if button.get_active():
            self.interval_minutes = minutes
            self.selected = None
            self._update_chart()

    def _is_cumulative(self):
        return self.today_cumulative if self.view == "today" else self.cumulative

    def _values(self):
        return chart_values(self.series, self._is_cumulative())

    def _update_chart(self):
        self.series = ()
        note = ""
        self.chart_message = "Waiting for usage…"
        if self.snapshot is not None:
            now = self.snapshot.refreshed_at
            if self.view == "today":
                self.subtitle.set_text(now.strftime("Today · %b %-d, %Y") + " · USD")
                usage = self.snapshot.intraday
                if usage is None:
                    self.chart_message = "Timestamped usage not loaded yet"
                elif usage.error:
                    self.chart_message = "Today's chart is unavailable"
                    note = usage.error
                else:
                    self.series = bucket_costs(usage.costs, now, self.interval_minutes)
                    self.chart_message = "No recorded spending today"
                    note = ("Local time · running total since midnight" if self.today_cumulative
                            else "Local time · current interval is incomplete")
                    if usage.unpriced:
                        note += f" · {usage.unpriced} usage records could not be priced"
                    total = sum(cost for _timestamp, cost in usage.costs)
                    if abs(total - self.snapshot.today_cost) >= 0.01:
                        note += f"\nTimestamped total: ${total:,.2f} · tray total: ${self.snapshot.today_cost:,.2f}"
            else:
                self.subtitle.set_text(now.strftime("%B %Y") + " · USD")
                self.series = self.snapshot.daily_costs
                self.chart_message = "No recorded spending this month"
        if self.selected is not None and self.selected >= len(self.series):
            self.selected = None
        self.chart_note.set_text(note)
        self.chart_note.set_visible(bool(note))
        self._update_detail()
        self.area.queue_draw()

    def _update_detail(self):
        if not self.series:
            self.detail.set_text(self.chart_message)
            return
        index = self.selected if self.selected is not None else len(self.series) - 1
        day, _cost = self.series[index]
        value = self._values()[index]
        amount = f"${value:,.4f}" if 0 < value < 0.01 else f"${value:,.2f}"
        if self.view == "today":
            end = datetime.fromtimestamp(day.timestamp() + self.interval_minutes * 60).astimezone()
            partial = " · so far" if index == len(self.series) - 1 else ""
            if self.today_cumulative:
                through = min(end, self.snapshot.refreshed_at.astimezone())
                description = f"Today through {through.strftime('%H:%M %Z')} · {amount}{partial}"
            else:
                description = f"{day.strftime('%H:%M %Z')}–{end.strftime('%H:%M %Z')} · {amount}{partial}"
        else:
            period = "Month through " if self.cumulative else ""
            partial = " · today so far" if day == self.snapshot.refreshed_at.date() else ""
            description = f"{period}{day.strftime('%b %-d')} · {amount}{partial}"
        self.detail.set_text(description)
        self.area.get_accessible().set_description(description)

    def _plot_bounds(self, width, height, context=None):
        values = self._values()
        label = self._axis_label(chart_scale(values))
        # Reserve enough room for dollar labels even with unusually large totals.
        left = max(58, context.text_extents(label)[2] + 14) if context else 58
        return left, 12, max(1, width - left - 28), max(1, height - 42)

    def _motion(self, area, event):
        if not self.series or self.plot_bounds is None:
            return False
        left, top, width, height = self.plot_bounds
        if left <= event.x <= left + width and top <= event.y <= top + height:
            count = len(self.series)
            fraction = (event.x - left) / width
            index = round(fraction * (count - 1)) if self._is_cumulative() else int(fraction * count)
            self.selected = max(0, min(count - 1, index))
        else:
            self.selected = None
        self._update_detail()
        area.queue_draw()
        return False

    def _leave(self, area, _event):
        self.selected = None
        self._update_detail()
        area.queue_draw()
        return False

    def _chart_key(self, area, event):
        if not self.series:
            return False
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
        accent = (0.12, 0.65, 0.57)
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
                bar_width = min(40, slot_width * (0.8 if slot_width >= 3 else 1))
                for index, (x, y) in enumerate(points):
                    cr.set_source_rgba(*accent, 1 if index == selected else 0.75)
                    cr.rectangle(x - bar_width / 2, y, bar_width, bottom - y)
                    cr.fill()

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
                label = self.series[index][0].strftime("%H:%M" if self.view == "today" else "%b %-d")
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
