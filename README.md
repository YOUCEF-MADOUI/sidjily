# SIDJILY

Socle initial d'une application Windows en français pour organiser des recherches sur le portail CNRC/Sidjilcom. Cette première étape fournit une application de bureau locale, une base SQLite et un gestionnaire de tâches reprenables. **Elle ne se connecte pas encore à Sidjilcom et ne réalise aucune collecte réseau.**

## Prérequis

- Python 3.10 ou supérieur
- Tkinter (généralement inclus avec Python sous Windows ; sur Linux, installer le paquet système `python3-tk` si nécessaire)

Le cœur du projet n'a pas de dépendance Python tierce à l'exécution.

## Lancer l'application

Depuis la racine du dépôt :

```bash
python -m pip install -e . --no-deps
sidjily
```

Ou sans installation :

```bash
PYTHONPATH=src python -m sidjily.main
```

Sous Windows PowerShell, la seconde commande s'écrit :

```powershell
$env:PYTHONPATH = "src"
python -m sidjily.main
```

La base `sidjily.sqlite3` et les journaux sont stockés dans le répertoire de données de l'utilisateur (`%LOCALAPPDATA%\SIDJILY` sous Windows, `$XDG_DATA_HOME/SIDJILY` ou `~/.local/share/SIDJILY` sous Linux). Aucun mot de passe Sidjilcom n'est demandé ni stocké.

## Tests et vérification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Les tests utilisent une base SQLite temporaire et n'envoient aucune requête réseau.

## État de cette première étape

- Interface initiale en français : création et suivi de recherches locales.
- SQLite versionnée : recherches, tâches hiérarchiques, résultats (schéma), paramètres et événements.
- États de tâche, suspension/reprise, journalisation des opérations et récupération des tâches laissées « en cours » après un arrêt.
- Architecture organisée pour séparer modèle, persistance, planification, interface et journaux.
- Le bouton de reprise remet une recherche dans la file persistante ; le moteur Sidjilcom, la navigation, l'extraction, le découpage automatique et les exports seront développés lors d'étapes distinctes.

Voir [docs/architecture.md](docs/architecture.md) pour les responsabilités et conventions du socle.
