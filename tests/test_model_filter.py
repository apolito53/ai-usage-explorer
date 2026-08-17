from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "ai-usage-explorer.sh"


def load_model_filter_code() -> dict:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    try:
        source = script.split("<<'PYTHON_EOF'\n", 1)[1].split("\nPYTHON_EOF", 1)[0]
    except IndexError as exc:
        raise AssertionError("Could not find embedded UI Python") from exc

    module = ast.parse(source)
    function_names = {
        "compact_model",
        "compact_provider",
        "infer_model_provider",
        "model_menu_marker",
        "ordered_providers",
        "parse_row_date",
        "row_date",
        "row_model_names",
        "row_provider_names",
        "toggle_model_group",
    }
    nodes = [
        node
        for node in module.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in function_names
        )
        or (
            isinstance(node, ast.ClassDef)
            and node.name == "UsageExplorer"
        )
    ]
    namespace = {
        "PROVIDER_LABELS": {"claude": "Claude", "codex": "Codex"},
        "PROVIDER_ORDER": ["claude", "codex"],
    }
    code = "\n\n".join(ast.get_source_segment(source, node) or "" for node in nodes)
    exec(
        compile(
            "from __future__ import annotations\n"
            "from datetime import datetime, timedelta\n"
            "from typing import Dict, List, Optional, Set\n\n"
            f"{code}\n",
            str(SCRIPT_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace


class ModelFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = load_model_filter_code()

    def explorer(self, models: list[str]):
        explorer = self.ui["UsageExplorer"].__new__(self.ui["UsageExplorer"])
        explorer.models = models
        explorer.state = SimpleNamespace(
            chart_offset=2,
            expanded=True,
            custom_range_end="",
            custom_range_start="",
            date_range="all",
            model_menu_open=True,
            model_menu_index=0,
            model_menu_selection=set(models),
            provider_index=0,
            selected=3,
            selected_models=None,
            sort_desc=True,
            sort_key="date",
            viewport=1,
        )
        return explorer

    def test_provider_rows_are_selectable_and_own_their_models(self):
        explorer = self.explorer([
            "claude-opus-5",
            "claude-sonnet-4-5",
            "gpt-5.3-codex",
        ])

        items = explorer.model_menu_items()
        provider_items = [item for item in items if item["type"] == "provider"]

        self.assertEqual(
            [
                ("claude", ["claude-opus-5", "claude-sonnet-4-5"]),
                ("codex", ["gpt-5.3-codex"]),
            ],
            [(item["provider"], item["models"]) for item in provider_items],
        )
        self.assertTrue(
            all(items.index(item) in explorer.model_menu_selectable_indices() for item in provider_items)
        )

    def test_provider_toggle_hides_and_restores_the_whole_group(self):
        models = ["claude-opus-5", "claude-sonnet-4-5", "gpt-5.3-codex"]
        explorer = self.explorer(models)
        items = explorer.model_menu_items()
        explorer.state.model_menu_index = next(
            index
            for index, item in enumerate(items)
            if item.get("provider") == "claude"
        )

        explorer.toggle_model_menu_selection()
        self.assertEqual({"gpt-5.3-codex"}, explorer.state.model_menu_selection)

        explorer.toggle_model_menu_selection()
        self.assertEqual(set(models), explorer.state.model_menu_selection)

    def test_partial_provider_toggle_selects_the_rest_of_the_group(self):
        models = ["claude-opus-5", "claude-sonnet-4-5", "gpt-5.3-codex"]
        explorer = self.explorer(models)
        explorer.state.model_menu_selection = {"claude-opus-5", "gpt-5.3-codex"}
        items = explorer.model_menu_items()
        explorer.state.model_menu_index = next(
            index
            for index, item in enumerate(items)
            if item.get("provider") == "claude"
        )

        explorer.toggle_model_menu_selection()

        self.assertEqual(set(models), explorer.state.model_menu_selection)

    def test_last_visible_provider_can_be_hidden(self):
        models = ["claude-opus-5", "claude-sonnet-4-5"]
        explorer = self.explorer(models)
        explorer.state.model_menu_index = next(
            index
            for index, item in enumerate(explorer.model_menu_items())
            if item.get("provider") == "claude"
        )

        explorer.toggle_model_menu_selection()

        self.assertEqual(set(), explorer.state.model_menu_selection)

    def test_group_marker_shows_partial_selection(self):
        marker = self.ui["model_menu_marker"]
        group = {"claude-opus-5", "claude-sonnet-4-5"}

        self.assertEqual("[x]", marker(set(group), group))
        self.assertEqual("[-]", marker({"claude-opus-5"}, group))
        self.assertEqual("[ ]", marker(set(), group))

    def test_applying_no_models_preserves_the_empty_filter(self):
        models = ["claude-opus-5", "gpt-5.3-codex"]
        explorer = self.explorer(models)
        explorer.state.model_menu_selection = set()

        explorer.apply_model_menu()

        self.assertEqual(set(), explorer.state.selected_models)
        self.assertFalse(explorer.state.model_menu_open)

    def test_applying_every_model_uses_the_all_models_sentinel(self):
        models = ["claude-opus-5", "gpt-5.3-codex"]
        explorer = self.explorer(models)

        explorer.apply_model_menu()

        self.assertIsNone(explorer.state.selected_models)

    def test_empty_applied_selection_filters_out_every_row(self):
        explorer = self.explorer(["claude-opus-5"])
        explorer.providers = ["claude"]
        explorer.rows = [{
            "date": "2026-08-17",
            "providers": ["claude"],
            "modelsUsed": ["claude-opus-5"],
        }]
        explorer.state.selected_models = set()

        self.assertEqual([], explorer.filtered_rows())
        self.assertEqual("None", explorer.model_filter_label())


if __name__ == "__main__":
    unittest.main()
