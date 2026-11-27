"""Réplique locale du portail Sidjilcom pour les tests de bout en bout.

Les pages sont servies par interception Playwright sous l'hôte officiel, sans réseau.
La structure suit les relevés réels documentés (formulaire POST dans le portlet
RechercheDetailleePortlet, suffixes de noms, autocomplétion YUI, liens de mode).
Les données d'entreprise sont fictives.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from sidjily.sidjilcom.autocomplete import AutocompleteTester
from sidjily.sidjilcom.autocomplete_playwright import PlaywrightAutocompleteDriver
from sidjily.sidjilcom.browser import PlaywrightBrowser
from sidjily.sidjilcom.config import SessionConfig
from sidjily.sidjilcom.selectors import DEFAULT_ENTERPRISE_SEARCH_ROUTE, SEARCH_MODE_PORTLET_MARKER

HOST = "https://sidjilcom.cnrc.dz"
NS = f"_{SEARCH_MODE_PORTLET_MARKER}_"
PAGE_SIZE = 10

WILAYAS = ["34000 : BORDJ BOU ARRERIDJ", "34100 : RAS EL OUED", "16000 : ALGER", "31000 : ORAN"]
ACTIVITES = ["442102 : COMMERCE DE GROS DE FRUITS", "442103 : COMMERCE DE GROS DE LEGUMES"]
NATIONALITES = ["ALGERIENNE", "FRANCAISE", "TUNISIENNE"]
FORMES = ["SARL", "EURL", "SPA", "SNC"]
SECTEURS = ["Commerce", "Industrie", "Services"]


@dataclass
class Company:
    ident: int
    rc: str
    name: str
    forme: str
    wilaya: str
    activite: str
    nif: str
    adresse: str


def _companies() -> list[Company]:
    rows = []
    for index in range(1, 24):
        rows.append(Company(
            ident=index,
            rc=f"34/00-{100000 + index:07d} B 19",
            name=f"{'AUTHENTIQUE ' if index == 1 else ''}SOCIETE {index:02d}",
            forme=FORMES[index % 2],
            wilaya=WILAYAS[0],
            activite=ACTIVITES[0],
            nif=f"0003400{index:08d}",
            adresse=f"{index} rue des Jardins, Bordj Bou Arreridj",
        ))
    rows.append(Company(99, "16/00-0999999 B 20", "AUTRE SPA", "SPA", WILAYAS[2], ACTIVITES[1],
                        "000160000000099", "Alger centre"))
    return rows


@dataclass
class PortalState:
    companies: list[Company] = field(default_factory=_companies)
    last_criteria: dict[str, str] = field(default_factory=dict)
    submissions: list[dict[str, str]] = field(default_factory=list)
    # Variante réaliste : l'option par défaut d'une liste porte une valeur non vide.
    secteur_placeholder_value: str = "0"
    date_input_type: str = "date"
    detail_links: bool = True
    session_expired: bool = False
    # Portail réel : la réponse à « Rechercher » arrive en AJAX après 10 à 20 s.
    results_delay_ms: int = 0
    # Portail réel : au-delà de 300 lignes, refus « Résultat de la Recherche Trop Grand ».
    max_results: int = 300
    last_page_html: str = ""


_AUTOCOMPLETE_JS = """
document.querySelectorAll('input.yui3-aclist-input').forEach(input => {
  const source = JSON.parse(input.dataset.source);
  const box = document.getElementById(input.getAttribute('aria-controls'));
  const list = box.querySelector('ul');
  input.addEventListener('input', () => {
    const query = input.value.trim().toUpperCase();
    list.innerHTML = '';
    const hits = query.length < 2 ? [] : source.filter(item => item.toUpperCase().includes(query));
    hits.forEach(text => {
      const li = document.createElement('li');
      li.className = 'yui3-aclist-item';
      li.setAttribute('role', 'option');
      li.textContent = text;
      li.addEventListener('mousedown', event => {
        event.preventDefault();
        input.value = text;
        box.classList.add('yui3-aclist-hidden');
        box.style.display = 'none';
        input.setAttribute('aria-expanded', 'false');
      });
      list.appendChild(li);
    });
    const show = hits.length > 0;
    box.style.display = show ? 'block' : 'none';
    input.setAttribute('aria-expanded', show ? 'true' : 'false');
  });
});
window.YUI = function () {};
"""


def _page(title: str, body: str, *, logged_in: bool = True) -> str:
    signout = '<a href="/c/portal/logout">Déconnexion</a>' if logged_in else ""
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:sans-serif}} .yui3-aclist{{position:absolute;background:#fff;border:1px solid #999}}
label{{display:inline-block;width:220px}} .row{{margin:4px}}</style></head>
<body><header><nav><a href="/">Accueil</a> <a href="{DEFAULT_ENTERPRISE_SEARCH_ROUTE}">Trouver une entreprise</a>
<a href="/group/sidjilcom/mon-tableau-de-bord">Nos abonnés / tableau de bord</a> {signout}</nav></header>
<main>{body}</main></body></html>"""


def _text(name: str, label: str, suffix: str) -> str:
    return (f'<div class="row"><label for="{NS}{suffix}">{label}</label>'
            f'<input type="text" id="{NS}{suffix}" name="{NS}{suffix}" value=""></div>')


def _autocomplete(label: str, suffix: str, source: list[str]) -> str:
    import json
    data = html.escape(json.dumps(source), quote=True)
    return (f'<div class="row"><label for="{NS}{suffix}">{label}</label>'
            f'<input type="text" id="{NS}{suffix}" name="{NS}{suffix}" value="" autocomplete="off" '
            f'class="field yui3-aclist-input" aria-autocomplete="list" aria-expanded="false" '
            f'aria-controls="{NS}{suffix}_aclist" data-source="{data}">'
            f'<div id="{NS}{suffix}_aclist" class="yui3-aclist" style="display:none">'
            f'<ul class="yui3-aclist-list" role="listbox"></ul></div></div>')


def _select(label: str, suffix: str, options: list[str], placeholder_value: str = "") -> str:
    items = f'<option value="{placeholder_value}">--Selectionnez--</option>' + "".join(
        f'<option value="{index + 1}">{html.escape(text)}</option>' for index, text in enumerate(options)
    )
    return (f'<div class="row"><label for="{NS}{suffix}">{label}</label>'
            f'<select id="{NS}{suffix}" name="{NS}{suffix}">{items}</select></div>')


def _radio(label: str, suffix: str, options: list[str]) -> str:
    items = "".join(
        f'<label><input type="radio" id="{NS}{suffix}" name="{NS}{suffix}" value="{1 - index}">'
        f'{html.escape(text)}</label>'
        for index, text in enumerate(options)
    )
    return f'<div class="row"><span>{label}</span>{items}</div>'


def _date(label: str, suffix: str, input_type: str) -> str:
    return (f'<div class="row"><label for="{NS}{suffix}">{label}</label>'
            f'<input type="{input_type}" id="{NS}{suffix}" name="{NS}{suffix}" value=""></div>')


def search_form(state: PortalState, mode: str) -> str:
    common = [
        '<fieldset><legend>Numéro d\'inscription</legend>',
        _text("", "nrc1", "nrc1"),
        _select("nrc2", "nrc2", ["A", "B"]),
        _text("", "nrc3", "nrc3"),
        _text("", "nrc4", "nrc4"),
        _select("nrc5", "nrc5", ["19", "20", "21"]),
        "</fieldset>",
        _autocomplete("Commune/Wilaya d'inscription", "wilcom", WILAYAS),
        _select("Secteur d'activité", "secteur", SECTEURS, state.secteur_placeholder_value),
        _autocomplete("Activité", "activite", ACTIVITES),
        _date("Date d'inscription du", "deb_immat", state.date_input_type),
        _date("au", "fin_immat", state.date_input_type),
        # Portail réel : boutons radio (deux <input> de même id/nom), pas des listes.
        _radio("Conformité RC", "CRCE", ["Oui", "Non"]),
        _radio("État commerçant", "etat_commercant", ["Actif", "Radié"]),
    ]
    if mode == "morale":
        specific = [
            _text("", "Raison Sociale / Nom commercial", "raison_social"),
            _select("Forme Juridique", "forme_juridi", FORMES),
            _text("", "Nom / Prénom du dirigeant", "nom_prenom_assoc"),
            _date("Date de naissance", "d_naiss", state.date_input_type),
            f'<div class="row"><label for="{NS}presume">Présumé</label>'
            f'<input type="checkbox" id="{NS}presume" name="{NS}presume"></div>',
            _autocomplete("Nationalité", "nation", NATIONALITES),
            _select("Qualité", "qualite_associe", ["Gérant", "Associé"]),
        ]
    else:
        specific = [
            _text("", "Nom", "nom"),
            _text("", "Prénom", "prenom"),
            _text("", "Nom commercial", "nom_com"),
            _date("Date de naissance", "d_naiss", state.date_input_type),
            f'<div class="row"><label for="{NS}presume">Présumé</label>'
            f'<input type="checkbox" id="{NS}presume" name="{NS}presume"></div>',
            _autocomplete("Nationalité", "nation", NATIONALITES),
        ]
    action = f"{DEFAULT_ENTERPRISE_SEARCH_ROUTE}?p_p_id={SEARCH_MODE_PORTLET_MARKER}&p_p_lifecycle=1&p_auth=ABCDEF"
    return (f'<form method="post" action="{html.escape(action)}" id="{NS}fm" name="{NS}fm">'
            f'<input type="hidden" name="{NS}mode" value="{mode}">'
            + "".join(common + specific)
            + '<div class="row"><button type="submit" class="btn btn-primary">Rechercher</button>'
              '<button type="reset" class="btn">Réinitialiser</button></div></form>')


# Bandeau d'aide du portail réel : role="alert" alors qu'il ne signale aucune erreur.
INTRO_ALERT = (
    '<div class="alert alert-info" role="alert">Cette fonctionnalité vous permet d’effectuer des recherches '
    'dans la banque de données nationale des entreprises (Commerçant personne physique et morale) au moyen '
    'de différents critères, tel que : N° RC, raison sociale, activité, commune ou wilaya d’inscription. '
    'En cas d’erreur de saisie, utilisez Nouvelle recherche.</div>'
)


def search_page(state: PortalState, mode: str | None, results_html: str = "") -> str:
    # Portail réel : les cartes de mode ne s'affichent que tant qu'aucun formulaire n'est choisi.
    links = "" if mode else (
        f'<div class="nav-modes"><a href="{DEFAULT_ENTERPRISE_SEARCH_ROUTE}?{NS}mode=physique">PERSONNES PHYSIQUES</a> '
        f'<a href="{DEFAULT_ENTERPRISE_SEARCH_ROUTE}?{NS}mode=morale">PERSONNES MORALES</a></div>'
    )
    form = search_form(state, mode) if mode else "<p>Choisissez un type de personne.</p>"
    body = (f'<section id="p_p_id{NS}" class="portlet-boundary portlet-boundary{NS}">'
            f'<h2 class="portlet-title">Recherche Commerçant</h2>{INTRO_ALERT}{links}{form}{results_html}</section>'
            f'<script>{_AUTOCOMPLETE_JS}</script>')
    return _page("Recherche Commerçant - Sidjilcom", body)


def _matches(company: Company, criteria: dict[str, str]) -> bool:
    def value(suffix: str) -> str:
        return criteria.get(f"{NS}{suffix}", "").strip()
    if value("raison_social") and value("raison_social").upper() not in company.name:
        return False
    if value("wilcom") and value("wilcom") != company.wilaya:
        return False
    if value("activite") and value("activite") != company.activite:
        return False
    forme = value("forme_juridi")
    if forme and forme.isdigit() and FORMES[int(forme) - 1] != company.forme:
        return False
    return True


def results_html(state: PortalState, page: int) -> str:
    hits = [company for company in state.companies if _matches(company, state.last_criteria)]
    if len(hits) > state.max_results:
        return ('<div class="alert alert-danger" role="alert"><strong>Erreur:</strong>'
                'Résultat de la Recherche Trop Grand ! Veuillez Affiner Votre Recherche</div>')
    if not hits:
        return '<div class="alert alert-info">Aucun résultat trouvé.</div>'
    pages = (len(hits) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, pages))
    rows = []
    for company in hits[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]:
        link = (f'<a href="{DEFAULT_ENTERPRISE_SEARCH_ROUTE}/-/fiche/{company.ident}?p_auth=XYZ987&jsessionid=S1">Détail</a>'
                if state.detail_links else '<a href="javascript:;" onclick="void(0)">Détail</a>')
        rows.append(
            f"<tr><td>{html.escape(company.rc)}</td><td>{html.escape(company.name)}</td>"
            f"<td>{company.forme}</td><td>{html.escape(company.wilaya)}</td><td>{link}</td></tr>"
        )
    def nav(label: str, target: int, enabled: bool) -> str:
        href = f"{DEFAULT_ENTERPRISE_SEARCH_ROUTE}?{NS}cur={target}" if enabled else "javascript:;"
        return f'<li class="{"" if enabled else "disabled"}"><a href="{href}">{label}</a></li>'
    pagination = (
        f'<div class="lfr-pagination"><span class="lfr-icon-menu-text">Page {page} de {pages}</span>'
        f'<ul class="pagination">{nav("Précédent", page - 1, page > 1)}{nav("Suivant", page + 1, page < pages)}</ul></div>'
        if pages > 1 else ""
    )
    first, last = (page - 1) * PAGE_SIZE + 1, min(page * PAGE_SIZE, len(hits))
    # Compteur tel qu'affiché par le portail réel (pas de « N résultats »).
    return (f'<small class="search-results">Affichage des résultats {first} - {last} parmi {len(hits)}.</small>'
            '<table class="table"><thead><tr><th>N° RC</th><th>Raison sociale</th><th>Forme juridique</th>'
            f'<th>Wilaya</th><th>Action</th></tr></thead><tbody>{"".join(rows)}</tbody></table>{pagination}')


def detail_page(company: Company) -> str:
    body = (f'<section><h2>{html.escape(company.name)}</h2><dl>'
            f'<dt>N° RC</dt><dd>{html.escape(company.rc)}</dd>'
            f'<dt>NIF</dt><dd>{company.nif}</dd>'
            f'<dt>Forme juridique</dt><dd>{company.forme}</dd>'
            f'<dt>Adresse</dt><dd>{html.escape(company.adresse)}</dd></dl>'
            '<table><tr><th>Activité principale</th><td>' + html.escape(company.activite) + '</td></tr>'
            '<tr><th>Capital social</th><td>100 000 DA</td></tr></table></section>')
    return _page(company.name, body)


class MockPortal:
    def __init__(self, state: PortalState | None = None) -> None:
        self.state = state or PortalState()

    def handle(self, route) -> None:  # noqa: ANN001 - objet Route Playwright
        request = route.request
        parts = urlsplit(request.url)
        query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
        path = parts.path.rstrip("/") or "/"
        state = self.state
        if path == "/web/sidjilcom/login" or state.session_expired:
            body = _page("Connexion", '<form><input type="text" name="login"><input type="password" name="pw">'
                         '<button>Se connecter</button></form>', logged_in=False)
        elif path == "/":
            body = _page("Accueil - Sidjilcom", "<h1>Bienvenue</h1>")
        elif path == DEFAULT_ENTERPRISE_SEARCH_ROUTE and request.method == "POST":
            form = {key: values[-1] for key, values in parse_qs(request.post_data or "", keep_blank_values=True).items()}
            state.last_criteria = form
            state.submissions.append(form)
            results = results_html(state, 1)
            if state.results_delay_ms:
                # Réponse AJAX lente du portail réel : les résultats n'apparaissent qu'après un délai.
                results = (f'<div id="ajax-results" style="display:none">{results}</div>'
                           f'<script>setTimeout(() => {{ document.getElementById("ajax-results")'
                           f'.style.display = "block"; }}, {state.results_delay_ms});</script>')
            body = search_page(state, form.get(f"{NS}mode", "morale"), results)
        elif path == DEFAULT_ENTERPRISE_SEARCH_ROUTE:
            mode = query.get(f"{NS}mode")
            if f"{NS}cur" in query:
                body = search_page(state, state.last_criteria.get(f"{NS}mode", "morale"),
                                   results_html(state, int(query[f"{NS}cur"])))
            else:
                body = search_page(state, mode)
        elif path.startswith(f"{DEFAULT_ENTERPRISE_SEARCH_ROUTE}/-/fiche/"):
            ident = int(path.rsplit("/", 1)[-1])
            company = next(item for item in state.companies if item.ident == ident)
            body = detail_page(company)
        elif path == "/group/sidjilcom/mon-tableau-de-bord":
            body = _page("Tableau de bord", "<h1>Tableau de bord</h1>")
        else:
            route.fulfill(status=404, body="introuvable")
            return
        state.last_page_html = body
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)


class MockPortalBrowser(PlaywrightBrowser):
    """Navigateur SIDJILY réel (Chromium sans affichage) branché sur la réplique."""

    def __init__(self, portal: MockPortal) -> None:
        super().__init__()
        self.portal = portal
        self._chromium = None

    def open(self, config: SessionConfig) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        executable = os.environ.get("SIDJILY_TEST_CHROMIUM") or None
        self._chromium = self._playwright.chromium.launch(headless=True, executable_path=executable)
        self._context = self._chromium.new_context()
        self._context.route(f"{HOST}/**", self.portal.handle)
        self._page = self._context.new_page()
        self._page.goto(config.url, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)
        self._autocomplete_tester = AutocompleteTester(PlaywrightAutocompleteDriver(self._page, config.url))

    def close(self) -> None:
        super().close()
        if self._chromium is not None:
            try:
                self._chromium.close()
            except Exception:
                pass
