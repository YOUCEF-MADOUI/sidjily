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
        self.assertIn("Aucune recherche n'a été envoyée à Sidjilcom.", source)

    def test_real_search_button_in_preparation_is_informational(self) -> None:
        dialog_node, _source = class_source("_NewSearchDialog")
        _init_node, init_source = method_source(dialog_node, "__init__")
        self.assertIn('text="Lancer la recherche"', init_source)
        self.assertIn('command=self._explain_execution_not_available', init_source)

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
