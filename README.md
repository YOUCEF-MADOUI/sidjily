# SIDJILY

Application de bureau en français pour organiser des recherches sur le portail CNRC/Sidjilcom. Elle fournit une base SQLite, un gestionnaire de tâches persistant et une première intégration de session Sidjilcom manuelle.

La session utilise Playwright/Chromium dans un profil SIDJILY séparé. **L'utilisateur saisit ses identifiants lui-même dans le navigateur visible.** SIDJILY ne lit pas les mots de passe, ne récupère pas le profil Chrome personnel et ne contourne ni authentification, ni CAPTCHA, ni protection anti-bot. Une recherche réelle n'est lancée qu'après récapitulatif et confirmation explicite. Les lignes de résultats sont ensuite collectées (pages suivantes comprises), les fiches entreprise peuvent être lues, et le tout s'exporte en CSV.

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

## Recherche réelle, collecte, fiches et export

Dans le préparateur, **Lancer la recherche** est distinct de **Enregistrer le brouillon**. Toute combinaison de critères est acceptée dès qu'au moins un critère est renseigné et que chaque champ utilisé a été confirmé sur le portail. Le récapitulatif exact est présenté dans une confirmation Oui/Non; Annuler ou répondre Non ne déclenche aucune soumission.

Après confirmation, SIDJILY vérifie le mode, le portlet et formulaire parent, chaque contrôle par son suffixe DOM, les valeurs relues dans le DOM, les champs obligatoires, puis l'unique bouton **Rechercher**. Pour une autocomplétion, une seule suggestion correspondant exactement au code saisi est acceptée. Toute absence, ambiguïté, valeur différente, navigation inattendue ou problème de session arrête le parcours avant le clic.

**Listes déroulantes.** Les options (forme juridique, secteur d'activité, conformité RC, état commerçant, qualité, composants du numéro d'inscription) ne sont pas codées en dur : connectez-vous, ouvrez **Trouver une entreprise**, puis cliquez **Capturer les listes du portail**. SIDJILY lit le type et les options de chaque contrôle pour les deux modes, sans rien saisir ni soumettre, les enregistre dans SQLite et les active dans le préparateur. Un champ reste grisé tant que le portail ne l'a pas montré comme liste avec options (ou comme champ texte pour les composants non confirmés). Recapturez si le portail change ses listes.

**Collecte.** Après la soumission, les lignes du tableau de résultats sont enregistrées, puis SIDJILY parcourt automatiquement les pages suivantes (50 pages au maximum) dans le navigateur, sans relancer la recherche. Les numéros (RC, NIF), téléphones et e-mails affichés sont conservés tels quels.

**Fiches entreprise.** Sélectionnez la recherche, puis **Résultats et fiches**. Pour chaque ligne qui comporte un lien de détail, **Lire les fiches sélectionnées** ou **Lire les fiches manquantes** (100 au maximum par lot, environ une fiche toutes les 1,5 s) ouvre la fiche dans Chromium et enregistre ses paires libellé/valeur. Les liens sont assainis : seul l'hôte officiel HTTPS est accepté, et les jetons (`p_auth`, CSRF) comme les identifiants de session sont retirés. La lecture s'arrête dès que la session expire.

**Export.** **Exporter CSV** produit un fichier UTF-8 avec séparateur `;` (ouverture directe dans Excel en français) : colonnes du tableau, puis une colonne par champ de fiche lu, puis le lien de la fiche.

La réservation persistante autorise une seule soumission possible à la fois. Une interruption au clic ou après lui garde la réservation et interdit tout nouvel essai; si l'étape est `result_unknown`, ne cliquez pas à nouveau sur **Lancer**. Sélectionnez la recherche et utilisez **Diagnostiquer le résultat (lecture seule)** uniquement si la page Sidjilcom est encore affichée; ce diagnostic n'envoie pas de recherche et son rapport s'ajoute à la fiche. Un échec démontré avant le clic devient `failed_before_submission` et n'est pas relancé automatiquement; un résultat non observable après le clic devient `result_unknown` et ne doit jamais être soumis de nouveau.

**Non testé sur le portail réel.** La capture des listes, la détection du lien « page suivante » et l'extraction des fiches s'appuient sur des structures HTML courantes (Liferay) et sont validées par des tests simulés. Le premier essai depuis votre PC dira si le portail les expose ainsi : par exemple, un lien de fiche en `javascript:` ne peut pas être suivi, et la fenêtre **Résultats et fiches** l'indique. Ne partagez jamais votre profil navigateur, cookies, mots de passe ni jetons.

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
- Recherche réelle sur confirmation avec tout critère confirmé; listes déroulantes capturées depuis le portail.
- Collecte des résultats paginés, lecture des fiches entreprise et export CSV.

Voir [docs/architecture.md](docs/architecture.md) et [docs/sidjilcom-navigation.md](docs/sidjilcom-navigation.md) pour les responsabilités, les routes relevées et les limites de validation.
