from __future__ import annotations

import ast
import unittest
from pathlib import Path


class ControlledSearchDiagnosticUiTests(unittest.TestCase):
    @staticmethod
    def _load_formatter():
        source_path = Path(__file__).parents[1] / "src" / "sidjily" / "ui" / "app.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        app_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SidjilyApp")
        method = next(
            node for node in app_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_format_controlled_search_failure_diagnostic"
        )
        method.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        scope: dict[str, object] = {}
        exec(compile(module, str(source_path), "exec"), scope)
        return scope[method.name]

    def test_structural_failure_report_displays_metadata_without_dom_values(self) -> None:
        formatter = self._load_formatter()
        report = formatter({
            "stage": "post_fill_verification",
            "reason_code": "filled_value_mismatch",
            "page": {"url": "https://sidjilcom.cnrc.dz/fr/group/sidjilcom/repertoire-des-commercants",
                     "title": "Recherche Commerçant", "section": "Trouver une entreprise"},
            "candidate_forms": [{
                "id": "legal-search", "name": "", "role": "search", "method": "POST",
                "action": "/fr/group/sidjilcom/repertoire-des-commercants",
                "parent_portlet_confirmed": True,
                "fields": [{"criterion": "activite", "name": "portlet_activi", "id": "activity",
                            "label": "Activité", "role": "combobox", "tag": "input", "type": "text",
                            "class": "yui3-aclist-input", "visible": True, "disabled": False,
                            "value_matches_criterion": False}],
                "button": {"candidate_count": 1, "visible": True, "enabled": True,
                           "associated": True, "id": "search", "type": "submit"},
                "required_invalid_controls": [],
            }],
            "autocomplete": {"field": "activite", "suffix": "_activi", "suggestion_count": 1,
                             "exact_code_match_count": 0},
        })
        joined = "\n".join(report)
        self.assertIn("filled_value_mismatch", joined)
        self.assertIn("portlet_activi", joined)
        self.assertIn("POST", joined)
        self.assertIn("Activité", joined)
        self.assertIn("candidats=1", joined)
        self.assertNotIn("442102", joined)
        self.assertNotIn("34000", joined)


if __name__ == "__main__":
    unittest.main()
