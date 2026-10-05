from __future__ import annotations

import ast
from pathlib import Path
import unittest


APP_PATH = Path(__file__).parents[1] / "src" / "sidjily" / "ui" / "app.py"
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)


def class_source(class_name: str) -> tuple[ast.ClassDef, str]:
    node = next(
        item for item in APP_TREE.body
        if isinstance(item, ast.ClassDef) and item.name == class_name
    )
    return node, ast.get_source_segment(APP_SOURCE, node) or ""


def method_source(class_node: ast.ClassDef, method_name: str) -> tuple[ast.FunctionDef, str]:
    node = next(
        item for item in class_node.body
        if isinstance(item, ast.FunctionDef) and item.name == method_name
    )
    return node, ast.get_source_segment(APP_SOURCE, node) or ""


class SearchPreparationUiContractTests(unittest.TestCase):
    def test_preparation_dialog_has_no_session_or_submission_dependency(self) -> None:
        dialog_node, source = class_source("_NewSearchDialog")
        called_attributes = {
            node.func.attr
            for node in ast.walk(dialog_node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("session_manager", source)
        self.assertNotIn("execute_search", called_attributes)
        self.assertNotIn("prepare_autocomplete_test", called_attributes)
        self.assertNotIn("select_autocomplete_suggestion", called_attributes)
        self.assertIn("Aucune requête réseau ni soumission", source)
        self.assertIn("askyesno", source)
        self.assertIn("launch_confirmed = True", source)
        self.assertIn("Lancer cette recherche réelle maintenant ?", source)

    def test_real_search_button_requires_valid_preview_and_calls_confirmation_flow(self) -> None:
        dialog_node, _source = class_source("_NewSearchDialog")
        _init_node, init_source = method_source(dialog_node, "__init__")
        _launch_node, launch_source = method_source(dialog_node, "_launch_search")
        self.assertIn('text="Lancer la recherche"', init_source)
        self.assertIn('command=self._launch_search', init_source)
        self.assertIn('state="disabled"', init_source)
        self.assertIn("askyesno", launch_source)
        self.assertIn("if not confirmed", launch_source)
        self.assertLess(launch_source.index("if not confirmed"), launch_source.index("self.launch_confirmed = True"))
        self.assertIn("self.window.destroy()", launch_source)

    def test_new_find_and_continue_actions_share_one_preparation_dialog(self) -> None:
        app_node, _source = class_source("SidjilyApp")
        _new_node, new_source = method_source(app_node, "new_search")
        _continue_node, continue_source = method_source(app_node, "resume_selected")
        _consume_node, consume_source = method_source(app_node, "_consume_session_operation")
        _find_node, find_source = method_source(app_node, "_open_enterprise_search")
        dialog_classes = [
            node for node in APP_TREE.body
            if isinstance(node, ast.ClassDef) and node.name == "_NewSearchDialog"
        ]
        self.assertEqual(len(dialog_classes), 1)
        self.assertIn("self._open_preparation_dialog()", new_source)
        self.assertNotIn("self._begin_preparation()", new_source)
        self.assertIn("self._open_preparation_dialog(draft)", continue_source)
        _start_node, start_source = method_source(app_node, "_start_confirmed_controlled_search")
        self.assertIn("confirmed=True", start_source)
        self.assertIn("on_pre_submit", start_source)
        self.assertIn("promote_draft_to_controlled_search", start_source)
        self.assertIn('self._pending_navigation_action = "prepare"', find_source)
        self.assertIn("self.session_manager.open_enterprise_search()", find_source)
        self.assertIn("self._open_preparation_dialog(draft)", consume_source)
        self.assertIn("diagnose_search_results", class_source("SidjilyApp")[1])
        self.assertIn("store_unknown_search_diagnostic", class_source("SidjilyApp")[1])

    def test_diagnostics_are_built_in_a_collapsible_developer_area(self) -> None:
        app_node, _source = class_source("SidjilyApp")
        _ui_node, ui_source = method_source(app_node, "_build_ui")
        _toggle_node, toggle_source = method_source(app_node, "_toggle_developer_tools")
        _developer_node, developer_source = method_source(app_node, "_build_developer_tools")
        self.assertIn('text="Outils développeur  ▸"', ui_source)
        self.assertIn('self.developer_content = ttk.LabelFrame', ui_source)
        self.assertIn("self.developer_content.pack_forget()", toggle_source)
        self.assertIn("self.developer_content.pack(", toggle_source)
        self.assertIn('text="Diagnostiquer la page"', developer_source)
        self.assertNotIn('text="Diagnostiquer la page"', ui_source)


if __name__ == "__main__":
    unittest.main()
