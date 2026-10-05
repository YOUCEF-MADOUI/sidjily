# SIDJILY

Application de bureau en français pour organiser des recherches sur le portail CNRC/Sidjilcom. Elle fournit une base SQLite, un gestionnaire de tâches persistant et une première intégration de session Sidjilcom manuelle.

La session utilise Playwright/Chromium dans un profil SIDJILY séparé. **L'utilisateur saisit ses identifiants lui-même dans le navigateur visible.** SIDJILY ne lit pas les mots de passe, ne récupère pas le profil Chrome personnel et ne contourne ni authentification, ni CAPTCHA, ni protection anti-bot. Une première recherche réelle strictement cadrée est disponible après récapitulatif et confirmation explicite; aucune collecte détaillée, pagination automatique, export ou reprise de recherche n'est implémentée.

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

Après ouverture du navigateur, utilisez **Diagnostiquer la page**, **Analyser personnes physiques / morales**, **Accueil Sidjilcom**, **Tableau de bord** et **Trouver une entreprise** pour valider la navigation. Le diagnostic inclut les métadonnées structurelles des champs, sélecteurs CSS possibles, contraintes `required`/`readonly`, formulaires/portlet, boutons et composants dynamiques; l'analyse des modes sélectionne uniquement les deux liens de type de personne et compare leurs formulaires. Ces diagnostics n'activent pas le bouton de recherche. Les routes et limites effectivement observées sont décrites dans [docs/sidjilcom-navigation.md](docs/sidjilcom-navigation.md).

### Diagnostic réel du formulaire personne morale (Tâche 13)

Le bouton **Diagnostiquer le formulaire réel** capture, sur demande explicite, la structure de la page déjà ouverte sans naviguer, remplir, sélectionner une suggestion ni soumettre. Il compare les captures successives et fournit un rapport copiable/sauvegardable sans valeurs de champs, données d'entreprise, cookies, jetons, réponses réseau ou endpoint/API direct. Mode d'emploi PC et limites : [docs/diagnostic-formulaire-reel.md](docs/diagnostic-formulaire-reel.md).

## Première recherche réelle contrôlée — Tâche 16

Dans le préparateur, **Lancer la recherche** est distinct de **Enregistrer le brouillon**. Pour cette unique tentative réelle, SIDJILY accepte uniquement **Personne morale**, **Activité = `442102`** et le critère conjoint **Commune/Wilaya = `34000`**. Aucun autre critère n'est permis. Le récapitulatif exact est présenté dans une confirmation Oui/Non; Annuler ou répondre Non ne ferme pas le préparateur et ne déclenche aucune soumission.

Après confirmation, SIDJILY vérifie le mode, le portlet et formulaire parent, les deux contrôles par leurs suffixes DOM observés (`_activi` et `_wilcom`), leurs valeurs relues dans le DOM, les champs obligatoires, puis l'unique bouton **Rechercher** associé au formulaire. Pour une autocomplétion, une suggestion n'est acceptée que si le code numérique confirmé est entier et suivi d'un séparateur explicite de libellé; il faut une seule suggestion ainsi correspondante, puis le DOM est revérifié. Une vérification finale du formulaire, des champs et du bouton précède immédiatement l'unique clic normal Playwright. Toute absence, ambiguïté, valeur différente, navigation inattendue ou problème de session arrête le parcours. Les blocages avant le clic montrent maintenant un diagnostic structurel (page, formulaire, champs, suggestion et bouton), sans valeurs saisies, cookies, jetons ni identifiants. Après le clic, seules les métadonnées structurelles sont observées (titre, URL assainie, zone/tableau/lignes, colonnes, compteur, pagination, absence de résultats et erreurs); aucune cellule d'entreprise n'est lue.

La réservation persistante autorise une seule soumission possible à la fois. Une interruption au clic ou après lui garde la réservation et interdit tout nouvel essai; si l'étape est `result_unknown`, ne cliquez pas à nouveau sur **Lancer**. Sélectionnez la recherche et utilisez **Diagnostiquer le résultat (lecture seule)** uniquement si la page Sidjilcom est encore affichée; ce diagnostic n'envoie pas de recherche et son rapport s'ajoute à la fiche. Un échec démontré avant le clic devient `failed_before_submission` et n'est pas relancé automatiquement; un résultat non observable après le clic devient `result_unknown` et ne doit jamais être soumis de nouveau.

**Tâche 18 — cause corrigée :** l'ancien contrôle comparait le texte intégral d'une suggestion au seul code numérique. Une suggestion rendue comme code suivi de son libellé officiel était donc rejetée comme non exacte. Le nouveau contrôle conserve le code exact comme critère, accepte seulement le séparateur de libellé explicitement reconnu, exige une suggestion unique et confirme le champ dans le DOM avant le clic. Cette cause est vérifiée dans le code et les tests simulés; elle n'a pas été observée lors d'une session PC par Arena.

**TEST RÉEL : NON EFFECTUÉ PAR ARENA.** Pour votre seul essai depuis votre PC : ouvrez SIDJILY, cliquez **Ouvrir Sidjilcom** et connectez-vous manuellement dans Chromium. Cliquez **Trouver une entreprise**; après ouverture de la page, SIDJILY affiche le préparateur. Si vous avez déjà cette page, vous pouvez également ouvrir le préparateur avec **Nouvelle recherche** ou **Continuer la recherche** sur un brouillon. Choisissez **Personne morale**, saisissez exactement `442102` dans **Activité** et `34000` dans **Commune/Wilaya**, cliquez **Préparer le récapitulatif**, puis **Lancer la recherche**. Vérifiez le récapitulatif et confirmez explicitement une seule fois. Attendez le rapport structurel; si le résultat devient inconnu, n'effectuez aucune nouvelle recherche. Ne partagez jamais votre profil navigateur, cookies, mots de passe ni jetons.

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
- Premier cycle de recherche réelle cadré par confirmation; diagnostic structurel uniquement, sans détails d'entreprise ni collecte/export.

Voir [docs/architecture.md](docs/architecture.md) et [docs/sidjilcom-navigation.md](docs/sidjilcom-navigation.md) pour les responsabilités, les routes relevées et les limites de validation.
