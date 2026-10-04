"""Interface Tkinter initiale : création et suivi local des recherches."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from sidjily.models import Search, Status
from sidjily.task_manager import InvalidTransition, TaskManager


STATUS_LABELS = {
    Status.PENDING: "En attente",
    Status.RUNNING: "En cours",
    Status.COMPLETED: "Terminée",
    Status.FAILED: "Échec",
    Status.RETRY: "À réessayer",
    Status.SUSPENDED: "Suspendue",
    Status.CANCELLED: "Annulée",
}


class SidjilyApp:
    def __init__(self, root: tk.Tk, manager: TaskManager):
        self.root = root
        self.manager = manager
        self.root.title("SIDJILY — Gestion des recherches")
        self.root.geometry("940x600")
        self.root.minsize(760, 460)
        self.selected_search_id: str | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="SIDJILY", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(
            container,
            text="Recherches persistantes — le connecteur Sidjilcom sera ajouté dans une prochaine étape.",
            wraplength=880,
        ).pack(anchor="w", pady=(2, 14))

        toolbar = ttk.Frame(container)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="Nouvelle recherche", command=self.new_search).pack(side="left")
        ttk.Button(toolbar, text="Continuer la recherche", command=self.resume_selected).pack(side="left", padx=8)
        ttk.Button(toolbar, text="Suspendre", command=self.pause_selected).pack(side="left")
        ttk.Button(toolbar, text="Actualiser", command=self.refresh).pack(side="right")

        columns = ("status", "progress", "created", "criteria")
        self.table = ttk.Treeview(container, columns=columns, show="tree headings", selectmode="browse")
        self.table.heading("#0", text="Recherche")
        self.table.heading("status", text="État")
        self.table.heading("progress", text="Tâches terminées")
        self.table.heading("created", text="Créée le (UTC)")
        self.table.heading("criteria", text="Critères")
        self.table.column("#0", width=210, minwidth=150)
        self.table.column("status", width=110, anchor="center")
        self.table.column("progress", width=125, anchor="center")
        self.table.column("created", width=150)
        self.table.column("criteria", width=300)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self._on_select)

        self.details = ttk.Label(container, text="Sélectionnez une recherche pour afficher son journal.", wraplength=890)
        self.details.pack(anchor="w", pady=(12, 0))
        self.footer = ttk.Label(container, text="Données enregistrées localement dans SQLite.", foreground="#555555")
        self.footer.pack(anchor="w", pady=(8, 0))

    def refresh(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        searches = self.manager.list_searches()
        for search in searches:
            tasks = self.manager.list_tasks(search.id)
            completed = sum(task.status == Status.COMPLETED for task in tasks)
            criteria = self._format_criteria(search.criteria)
            self.table.insert(
                "", "end", iid=search.id,
                text=search.name,
                values=(STATUS_LABELS[search.status], f"{completed}/{len(tasks)}", search.created_at[:19], criteria),
            )
        if self.selected_search_id and self.selected_search_id in self.table.get_children():
            self.table.selection_set(self.selected_search_id)
            self._show_events(self.selected_search_id)
        else:
            self.selected_search_id = None
            self.details.configure(text="Sélectionnez une recherche pour afficher son journal.")

    def new_search(self) -> None:
        dialog = _NewSearchDialog(self.root)
        self.root.wait_window(dialog.window)
        if dialog.result is None:
            return
        name, criteria = dialog.result
        self.manager.create_search(name, criteria)
        self.refresh()
        self.footer.configure(text="Recherche enregistrée en attente. Aucun accès réseau n'est effectué dans cette version.")

    def resume_selected(self) -> None:
        search = self._selected_search()
        if search is None:
            return
        try:
            self.manager.resume_search(search.id)
        except InvalidTransition as exc:
            messagebox.showinfo("Reprise impossible", str(exc), parent=self.root)
            return
        self.refresh()
        messagebox.showinfo(
            "Recherche prête",
            "La progression persistée est conservée et la recherche est remise en attente. "
            "Le moteur de collecte Sidjilcom n'est pas encore activé dans cette première étape.",
            parent=self.root,
        )

    def pause_selected(self) -> None:
        search = self._selected_search()
        if search is None:
            return
        try:
            self.manager.pause_search(search.id)
        except InvalidTransition as exc:
            messagebox.showinfo("Suspension impossible", str(exc), parent=self.root)
            return
        self.refresh()

    def _selected_search(self) -> Search | None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("Aucune sélection", "Sélectionnez d'abord une recherche.", parent=self.root)
            return None
        return self.manager.get_search(selected[0])

    def _on_select(self, _event: Any) -> None:
        selected = self.table.selection()
        self.selected_search_id = selected[0] if selected else None
        if self.selected_search_id:
            self._show_events(self.selected_search_id)

    def _show_events(self, search_id: str) -> None:
        events = self.manager.list_events(search_id, limit=4)
        if not events:
            self.details.configure(text="Aucun événement enregistré pour cette recherche.")
            return
        recent = "   •   ".join(f"{event['created_at'][11:19]} {event['message']}" for event in events[-3:])
        self.details.configure(text=f"Journal récent : {recent}")

    @staticmethod
    def _format_criteria(criteria: dict[str, Any]) -> str:
        return " · ".join(f"{key}: {value}" for key, value in criteria.items() if value) or "—"


class _NewSearchDialog:
    def __init__(self, parent: tk.Tk):
        self.result: tuple[str, dict[str, Any]] | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Nouvelle recherche")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        self.name = tk.StringVar(value="Nouvelle recherche")
        self.person_type = tk.StringVar(value="Personne morale")
        self.activity = tk.StringVar()
        self.wilaya = tk.StringVar()
        self.commune = tk.StringVar()
        self.period = tk.StringVar()
        fields = [
            ("Nom de la recherche", self.name),
            ("Type de personne", self.person_type),
            ("Code activité", self.activity),
            ("Wilaya", self.wilaya),
            ("Commune", self.commune),
            ("Période (facultatif)", self.period),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            if label == "Type de personne":
                ttk.Combobox(
                    frame, textvariable=variable, values=("Personne morale", "Personne physique"),
                    state="readonly", width=32,
                ).grid(row=row, column=1, sticky="ew", pady=5)
            else:
                ttk.Entry(frame, textvariable=variable, width=36).grid(row=row, column=1, sticky="ew", pady=5)
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Annuler", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="Créer", command=self._submit).pack(side="right", padx=8)
        self.window.bind("<Return>", lambda _event: self._submit())

    def _submit(self) -> None:
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning("Nom requis", "Saisissez un nom pour cette recherche.", parent=self.window)
            return
        criteria = {
            "type_personne": self.person_type.get(),
            "code_activite": self.activity.get().strip() or None,
            "wilaya": self.wilaya.get().strip() or None,
            "commune": self.commune.get().strip() or None,
            "periode": self.period.get().strip() or None,
        }
        self.result = name, criteria
        self.window.destroy()
