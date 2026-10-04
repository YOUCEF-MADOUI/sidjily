# SIDJILY

Application de bureau en français pour organiser des recherches sur le portail CNRC/Sidjilcom. Elle fournit une base SQLite, un gestionnaire de tâches persistant et une première intégration de session Sidjilcom manuelle.

La session utilise Playwright/Chromium dans un profil SIDJILY séparé. **L'utilisateur saisit ses identifiants lui-même dans le navigateur visible.** SIDJILY ne lit pas les mots de passe, ne récupère pas le profil Chrome personnel et ne contourne ni authentification, ni CAPTCHA, ni protection anti-bot. Le moteur de recherche et la collecte ne sont pas encore implémentés.

## Prérequis

- Python 3.10 ou supérieur, avec Tkinter
- Chromium géré par Playwright

## Installation et démarrage

Depuis la racine du dépôt :

```bash
python -m pip install -e .
python -m playwright install chromium
sidjily
```

Ou sans installation du paquet :

```bash
python -m pip install 'playwright>=1.48,<2'
python -m playwright install chromium
PYTHONPATH=src python -m sidjily.main
```

Sous Windows PowerShell, pour le lancement sans installation :

```powershell
python -m pip install 'playwright>=1.48,<2'
python -m playwright install chromium
$env:PYTHONPATH = "src"
python -m sidjily.main
```

Sur Linux, installez également le paquet système Tkinter (`python3-tk`) si nécessaire. Playwright peut indiquer des dépendances système manquantes selon la distribution.

## Connexion manuelle

Dans la zone **Connexion Sidjilcom**, sélectionnez **Ouvrir Sidjilcom**. Une fenêtre Chromium visible s'ouvre sur le portail officiel; SIDJILY vérifie ensuite automatiquement l'accès en ouvrant la route « Trouver une entreprise » (sans remplir ni soumettre le formulaire). Si Sidjilcom demande une connexion, saisissez vous-même vos identifiants; l'application ne lit pas les valeurs des champs d'identifiants. Après authentification, le retour à la route protégée permet au statut de passer automatiquement à « Connecté ». Le contexte persistant dédié est enregistré dans le répertoire de données utilisateur :

- Windows : `%LOCALAPPDATA%\SIDJILY\browser_profile`
- Linux : `$XDG_DATA_HOME/SIDJILY/browser_profile` ou `~/.local/share/SIDJILY/browser_profile`

La session demeure locale. **Ne partagez pas ce dossier** : il contient l'état d'authentification du navigateur. Le bouton **Déconnecter** ferme le navigateur et conserve ce profil pour le prochain lancement; il n'effectue pas de déconnexion du portail. Une reconnexion exigée par Sidjilcom est signalée à l'écran. Un fichier marqueur vide, sans identifiant ni jeton, permet de reconnaître après redémarrage qu'une connexion avait déjà réussi.

Les recherches et les journaux restent dans `sidjily.sqlite3` et `logs/` sous le même répertoire de données. Aucun mot de passe Sidjilcom n'est enregistré dans SQLite ou les journaux.

Après ouverture du navigateur, utilisez **Diagnostiquer la page**, **Analyser personnes physiques / morales**, **Accueil Sidjilcom**, **Tableau de bord** et **Trouver une entreprise** pour valider la navigation. Le diagnostic inclut les métadonnées structurelles des champs, sélecteurs CSS possibles, contraintes `required`/`readonly`, formulaires/portlet, boutons et composants dynamiques; l'analyse des modes sélectionne uniquement les deux liens de type de personne et compare leurs formulaires. Aucune valeur de champ n'est lue, aucun endpoint n'est appelé manuellement et aucun bouton de recherche n'est activé. Les routes et limites effectivement observées sont décrites dans [docs/sidjilcom-navigation.md](docs/sidjilcom-navigation.md).

## Configuration navigateur

La configuration `SessionConfig` accepte l'URL officielle, le chemin du profil, le mode d'affichage et les temporisations. Les valeurs par défaut sont le portail officiel, le profil privé de SIDJILY et `headless=False` (navigateur visible).

Variables d'environnement facultatives :

- `SIDJILY_BROWSER_HEADLESS=true|false`
- `SIDJILY_BROWSER_PROFILE=/chemin/vers/un/profil-dedie` (hors du dépôt et différent d'un profil Chrome/Chromium personnel)
- `SIDJILY_SIDJILCOM_URL=https://sidjilcom.cnrc.dz/` (hôte HTTPS officiel seulement)

## Tests et vérification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Les tests de session injectent un navigateur simulé et n'ont besoin ni d'un compte Sidjilcom, ni d'un navigateur Playwright installé; ils n'envoient pas de requête au portail.

## Périmètre actuel

- Interface française : tâches locales et état de connexion Sidjilcom.
- SQLite versionnée, journalisation et reprise des tâches interrompues (socle précédent).
- Session persistante manuelle dans un profil Chromium SIDJILY dédié, avec détection prudente de connexion et d'expiration.
- Navigation de validation vers l'accueil, le tableau de bord et « Trouver une entreprise »; diagnostic des libellés visibles sans leurs valeurs.
- Aucune recherche n'est soumise; aucune extraction, subdivision des résultats, export ou IA à ce stade.

Voir [docs/architecture.md](docs/architecture.md) et [docs/sidjilcom-navigation.md](docs/sidjilcom-navigation.md) pour les responsabilités, les routes relevées et les limites de validation.
