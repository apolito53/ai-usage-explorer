from __future__ import annotations

import ast
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "ai-usage-explorer.sh"


def embedded_python(marker: str, terminator: str) -> str:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    try:
        return script.split(marker, 1)[1].split(terminator, 1)[0]
    except IndexError as exc:
        raise AssertionError(f"Could not find embedded Python after {marker!r}") from exc


def load_functions(source: str, names: set[str]) -> dict:
    module = ast.parse(source)
    functions = {
        node.name: node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    }
    missing = names - functions.keys()
    if missing:
        raise AssertionError(f"Missing embedded functions: {sorted(missing)}")

    function_source = "\n\n".join(
        ast.get_source_segment(source, node) or ""
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    )
    namespace: dict = {}
    exec(
        compile(
            "from __future__ import annotations\n"
            "from datetime import datetime\n"
            "from typing import Dict, Optional\n\n"
            f"{function_source}\n",
            str(SCRIPT_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace


class PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        updater_source = embedded_python(
            '<<\'PY\' || echo "Pricing update skipped; using cached rates if available." >&2\n',
            "\nPY\n}",
        )
        cls.updater = load_functions(updater_source, {"builtin_price_cards"})

        ui_source = embedded_python(
            "<<'PYTHON_EOF'\n",
            "\nPYTHON_EOF",
        )
        cls.ui = load_functions(
            ui_source,
            {
                "backfill_missing_costs",
                "estimated_model_cost",
                "model_pricing_record",
                "parse_row_date",
                "pricing_snapshot_for_date",
                "rate_value",
                "row_date",
                "simplify_model_name",
            },
        )

    def test_builtin_pricing_includes_opus_5(self):
        self.assertEqual(
            {
                "input": 5.0,
                "output": 25.0,
                "cacheWrite": 6.25,
                "cacheRead": 0.5,
            },
            self.updater["builtin_price_cards"]()["claude-opus-5"],
        )

    def test_zero_cost_is_backfilled_from_the_active_snapshot(self):
        usage = {
            "daily": [
                {
                    "date": "2026-07-24",
                    "totalCost": 0.0,
                    "modelBreakdowns": [
                        {
                            "modelName": "claude-opus-5",
                            "inputTokens": 28,
                            "outputTokens": 36_215,
                            "cacheCreationTokens": 681_703,
                            "cacheReadTokens": 2_705_548,
                            "cost": 0.0,
                        }
                    ],
                }
            ],
            "totals": {"totalCost": 0.0},
        }
        history = {
            "snapshots": [
                {
                    "effectiveDate": "2026-07-24",
                    "models": {
                        "claude-opus-5": {
                            "rates": {
                                "input": 5.0,
                                "output": 25.0,
                                "cacheWrite": 6.25,
                                "cacheRead": 0.5,
                            }
                        }
                    },
                }
            ]
        }

        updated = self.ui["backfill_missing_costs"](usage, history)

        expected = (
            28 * 5.0
            + 36_215 * 25.0
            + 681_703 * 6.25
            + 2_705_548 * 0.5
        ) / 1_000_000
        self.assertEqual(1, updated)
        self.assertAlmostEqual(
            expected,
            usage["daily"][0]["modelBreakdowns"][0]["cost"],
        )
        self.assertAlmostEqual(expected, usage["daily"][0]["totalCost"])
        self.assertAlmostEqual(expected, usage["totals"]["totalCost"])

    def test_existing_nonzero_cost_remains_authoritative(self):
        usage = {
            "daily": [
                {
                    "date": "2026-07-24",
                    "totalCost": 3.25,
                    "modelBreakdowns": [
                        {
                            "modelName": "claude-opus-5",
                            "inputTokens": 1_000_000,
                            "outputTokens": 1_000_000,
                            "cacheCreationTokens": 0,
                            "cacheReadTokens": 0,
                            "cost": 3.25,
                        }
                    ],
                }
            ],
            "totals": {"totalCost": 3.25},
        }
        history = {
            "snapshots": [
                {
                    "effectiveDate": "2026-07-24",
                    "models": {
                        "claude-opus-5": {
                            "rates": {
                                "input": 5.0,
                                "output": 25.0,
                                "cacheWrite": 6.25,
                                "cacheRead": 0.5,
                            }
                        }
                    },
                }
            ]
        }

        updated = self.ui["backfill_missing_costs"](usage, history)

        self.assertEqual(0, updated)
        self.assertEqual(3.25, usage["daily"][0]["modelBreakdowns"][0]["cost"])
        self.assertEqual(3.25, usage["daily"][0]["totalCost"])
        self.assertEqual(3.25, usage["totals"]["totalCost"])


if __name__ == "__main__":
    unittest.main()
