# Architecture SIDJILY

## Organisation

```text
src/sidjily/
  main.py                Démarrage, récupération des tâches et injection des services
  paths.py               Répertoires locaux utilisateur et profil Chromium dédié
  logging_config.py      Journal de fichier avec rotation
  models.py              Modèles et états des tâches
  database.py            Connexions SQLite, migrations versionnées et événements
  task_manager.py        Cycle de vie des recherches et tâches persistantes
  sidjilcom/
    config.py             URL officielle, profil et options Chromium
    browser.py            Adaptateur Playwright isolé derrière un protocole
    selectors.py          Indices DOM et classification conservatrice de session
    criteria.py           Modèles, validation et mapping central des champs
    autocomplete.py       Cycle d'autocomplétion injectable
    search.py             Orchestration prudente, confirmation, étapes et diagnostic
    search_playwright.py  Remplissage DOM et observation structurelle du formulaire
    session.py            Cycle de vie thread-safe et états publics
  ui/app.py               Interface Tkinter française, critères, confirmation et session Sidjilcom
tests/
  test_task_manager.py
  test_sidjilcom_session.py
```

## Session Sidjilcom

1. L'interface demande au gestionnaire de session d'ouvrir Chromium dans un thread dédié.
2. Playwright lance un contexte persistant dans `browser_profile_dir()`, jamais dans le profil Chrome de l'utilisateur, puis ouvre l'URL HTTPS officielle.
3. Au démarrage, le navigateur vérifie l'accès avec une navigation GET vers la route protégée « Trouver une entreprise ». Le formulaire accessible confirme une session déjà active; une redirection vers login conserve l'état d'attente.
4. L'utilisateur s'authentifie manuellement dans cette fenêtre. Le code ne lit pas la valeur des champs, les cookies, le stockage web ou les mots de passe; après connexion, le retour du portail vers la route demandée est détecté par le polling.
5. Le navigateur vérifie ensuite le domaine courant, la présence d'un champ mot de passe, quelques avis d'expiration visibles et des marqueurs sémantiques de déconnexion. Après une confirmation positive durant l'exécution, les pages Sidjilcom sans marqueur de menu ne font pas retomber l'état à « Connexion en attente »; un formulaire/login ou avis d'expiration garde priorité.
6. Un indicateur de connexion positive est requis avant d'annoncer « Connecté ». Les indices DOM sont centralisés dans `sidjilcom/selectors.py` afin de les adapter si le portail évolue.
7. Après une connexion confirmée, un marqueur local vide est créé dans le profil. Il indique uniquement qu'une session avait déjà été établie et permet de distinguer une demande de reconnexion après redémarrage. Le marqueur seul ne confirme jamais une session active.
8. Les commandes Accueil, Tableau de bord et Trouver une entreprise passent par une file de commandes du thread Playwright. Le tableau de bord/la page métier ne confirment l'accès qu'après vérification du contenu réel; une redirection vers login est rapportée.
9. Le diagnostic retourne des métadonnées DOM assainies des formulaires et contrôles visibles (jamais leurs valeurs), y compris name/id, sélecteur CSS, contraintes required/readonly, formulaire parent (id/name/action/méthode), portlet et frame, datalist, composants et endpoints déclarés. L'analyse des modes sélectionne uniquement les liens PERSONNES PHYSIQUES/MORALES, attend la stabilisation du portlet et compare les instantanés sans activer les boutons métier ni appeler directement les endpoints.
10. La fermeture ferme Chromium et conserve le profil local. Hors recherche contrôlée en cours, une session expirée ne modifie pas les recherches existantes; pendant ce flux, l'étape et l'erreur d'arrêt sont persistées sans relance.

États affichés : `DISCONNECTED`, `CONNECTING`, `WAITING_FOR_LOGIN`, `CONNECTED`, `SESSION_EXPIRED`, `ERROR`; `DISCONNECTING` est un état transitoire de fermeture.

## Première recherche contrôlée

Le préparateur général conserve les dataclasses et le registre `SIDJILCOM_CONTROL_MAP`; il n'existe pas de second catalogue de champs. Le bouton réel est limité par une validation centrale à Personne morale + Activité `442102` + critère conjoint Commune/Wilaya `34000`, sans autre critère. Une confirmation Oui/Non explicite précède toute action Playwright. SQLite conserve une réservation singleton après le début possible du clic; toute interruption ambiguë interdit une seconde soumission. Une erreur démontrée avant le stade `submitting` libère la réservation, sans relancer automatiquement l'ancienne tâche.

La fenêtre est modale : l'utilisateur prépare le récapitulatif, clique explicitement **Lancer la recherche**, puis confirme. Seulement après ce consentement, le thread navigateur sélectionne le lien de mode, re-résout les champs par suffixe observé, refuse tout champ absent/ambigu/prérempli, utilise le `AutocompleteTester` existant et sélectionne une suggestion qui correspond exactement à la valeur confirmée. Il n'existe qu'un seul point de clic sur l'unique bouton visible/enabled **Rechercher** du formulaire identifié. Toute incertitude arrête l'opération; le flux contrôlé n'est ni reprenable ni relançable automatiquement.

Après le clic, le pilote n'expose que titre, URL sans query/fragment, compteur éventuel, nombre de tableaux/lignes, en-têtes, pagination, message d'absence de résultat, alertes et état de session. Le JavaScript de diagnostic ne lit pas les cellules du tableau. Aucun détail d'entreprise, collecte, découpage, pagination ou export n'est présent.

`TaskManager` persiste l'identifiant, le mode/critères, l'état, les dates, l'étape, l'erreur fixe et un résumé structurel optionnel. La migration SQLite version 3 ajoute la réservation persistante de la recherche contrôlée; la version 2 avait ajouté `searches.step` et `searches.result_summary_json`. La date, les critères, les étapes, le diagnostic préalable assaini et le résumé structurel restent locaux. Les profils, valeurs DOM diagnostiques, cookies, jetons et identifiants de session ne sont jamais enregistrés. Une interruption au clic ou après lui devient `result_unknown`, sans reprise; un diagnostic structurel ultérieur n'exécute pas le moteur de recherche.

## Configuration

`SessionConfig` impose HTTPS et l'hôte `sidjilcom.cnrc.dz`; il refuse les credentials, paramètres et fragments dans l'URL. Par défaut, Chromium est visible et le profil est placé dans le répertoire de données utilisateur SIDJILY. `SIDJILY_BROWSER_HEADLESS`, `SIDJILY_BROWSER_PROFILE` et `SIDJILY_SIDJILCOM_URL` permettent de configurer ces options sans coder de secrets.

## Sécurité et limites

Le dossier de profil est un secret local au même titre qu'un état de connexion : ne pas le partager ni le committer. `.gitignore` exclut le profil, cookies, fichiers de stockage Playwright, la base et les logs. Les événements de session ne contiennent que les changements d'état; les messages de page, URL courantes, valeurs de formulaire et exceptions complètes ne sont jamais journalisés.

Le détecteur est volontairement prudent : il s'appuie sur un marqueur de session utilisateur ou sur l'accès au contenu d'une route protégée et traite une page de connexion après une session établie comme expirée. Il ne résout ni CAPTCHA ni protection anti-bot. Les textes/attributs du portail peuvent changer; une validation réelle avec un compte autorisé reste nécessaire. Si aucun marqueur positif ni contenu protégé n'est observé, le détecteur reste en attente au lieu d'annoncer une connexion non confirmée.

## Recherches et schéma SQLite

`TaskManager` persiste les recherches, tâches, événements et progression. `PRAGMA user_version` identifie le schéma; la version 2 ajoute une migration non destructive pour l'étape et le résumé du diagnostic structurel. Toute évolution future devra ajouter une migration versionnée et ne pas recréer la base existante.
