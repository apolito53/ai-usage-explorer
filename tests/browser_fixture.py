"""Synthetic chart data for the optional Playwright smoke test. No local logs."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_usage_intraday import IntradayUsage
from ai_usage_web import UsageGraphServer
from test_web import sample_snapshot


def fixture():
    snapshot = sample_snapshot()
    snapshot.refreshed_at = datetime(2024, 3, 20, 12, 30)
    snapshot.monthly_daily_costs = {}
    snapshot.daily_model_costs = {}
    snapshot.intraday_days = {}
    for month, count in ((date(2024, 1, 1), 31), (date(2024, 2, 1), 29), (date(2024, 3, 1), 20)):
        days = []
        for i in range(count):
            day = month + timedelta(days=i)
            opus = round((i % 5 + 1) * 1.2, 2) if month.month != 1 else 0
            sonnet = round((i % 3 + 1) * .7, 2) if month.month != 1 else 0
            days.append((day, opus + sonnet))
            snapshot.daily_model_costs[day] = {"claude-opus": opus, "claude-sonnet": sonnet}
            stamp = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15).astimezone()
            later = stamp + timedelta(hours=2)
            snapshot.intraday_days[day] = IntradayUsage(
                costs=((stamp, opus), (later, sonnet)),
                model_costs=((stamp, "claude-opus", opus), (later, "claude-sonnet", sonnet)),
            )
        snapshot.monthly_daily_costs[month] = tuple(days)
    snapshot.daily_costs = snapshot.monthly_daily_costs[date(2024, 3, 1)]
    snapshot.month_cost = sum(cost for _day, cost in snapshot.daily_costs)
    snapshot.today_cost = snapshot.daily_costs[-1][1]
    snapshot.intraday = snapshot.intraday_days[snapshot.refreshed_at.date()]
    return snapshot


if __name__ == "__main__":
    calls = []
    graphs = UsageGraphServer(lambda: calls.append(True), project="Browser smoke fixture")
    print(json.dumps({"url": graphs.start()}), flush=True)
    try:
        for command in sys.stdin:
            command = command.strip()
            if command == "ready":
                graphs.set_usage(fixture())
                graphs.set_status("")
            elif command == "error":
                graphs.set_status("Refresh failed: simulated offline")
            elif command == "missing":
                snapshot = fixture()
                snapshot.refreshed_at += timedelta(seconds=1)
                snapshot.intraday = IntradayUsage(error="No local Claude projects directory found")
                snapshot.intraday_days = {}
                graphs.set_usage(snapshot)
            elif command == "rollover":
                snapshot = fixture()
                snapshot.refreshed_at = datetime(2024, 4, 1, 12)
                snapshot.monthly_daily_costs[date(2024, 4, 1)] = ((date(2024, 4, 1), 0),)
                snapshot.daily_costs = snapshot.monthly_daily_costs[date(2024, 4, 1)]
                snapshot.month_cost = snapshot.today_cost = 0
                snapshot.intraday = IntradayUsage()
                graphs.set_usage(snapshot)
            elif command == "stop":
                break
            print(json.dumps({"command": command, "refreshes": len(calls)}), flush=True)
    finally:
        graphs.close()
