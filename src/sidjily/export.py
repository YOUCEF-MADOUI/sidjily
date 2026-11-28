"""Construction du tableau exporté : colonnes des résultats puis champs des fiches lues."""

from __future__ import annotations

import csv
from typing import Any


def build_export_table(results: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    if not results:
        return [], []
    base_columns = [str(column) for column in results[0].get("columns", [])]
    detail_labels: list[str] = []
    for result in results:
        detail = result.get("detail") or {}
        for label, _value in detail.get("fields", []):
            if label not in detail_labels:
                detail_labels.append(label)
    header = base_columns + [f"Fiche — {label}" for label in detail_labels]
    if any(result.get("detail_url") for result in results):
        header.append("Lien fiche")
    rows: list[list[str]] = []
    for result in results:
        values = [str(value) for value in result.get("values", [])]
        values = (values + [""] * len(base_columns))[:len(base_columns)]
        detail_values: dict[str, str] = {}
        for label, value in (result.get("detail") or {}).get("fields", []):
            # Un libellé répété sur une fiche est concaténé plutôt que perdu.
            detail_values[label] = f"{detail_values[label]} | {value}" if label in detail_values else value
        row = values + [detail_values.get(label, "") for label in detail_labels]
        if header and header[-1] == "Lien fiche":
            row.append(str(result.get("detail_url", "")))
        rows.append(row)
    return header, rows


def write_csv(path: str, results: list[dict[str, Any]]) -> int:
    header, rows = build_export_table(results)
    with open(path, "w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    return len(rows)
