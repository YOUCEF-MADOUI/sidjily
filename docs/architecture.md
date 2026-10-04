# Architecture SIDJILY

## Organisation

```text
src/sidjily/
  main.py                Démarrage, récupération des tâches et injection des services
  paths.py               Répertoires locaux utilisateur et profil Chromium dédié
  logging_config.py      Journal de fichier avec rotation
  models.py              Modèles et états des tâches
  database.py            Connexions SQLite, schéma initial et événements
  task_manager.py        Cycle de vie des recherches et tâches persistantes
  sidjilcom/
    config.py             URL officielle, profil et options Chromium
    browser.py            Adaptateur Playwright isolé derrière un protocole
    selectors.py          Indices DOM et classification conservatrice de session
    session.py             Cycle de vie thread-safe et états publics
  ui/app.py              Interface Tkinter française, tâches et connexion Sidjilcom
tests/
  test_task_manager.py
  test_sidjilcom_session.py
```

## Session Sidjilcom

1. L'interface demande au gestionnaire de session d'ouvrir Chromium dans un thread dédié.
2. Playwright lance un contexte persistant dans `browser_profile_dir()`, jamais dans le profil Chrome de l'utilisateur, puis ouvre l'URL HTTPS officielle.
3. L'utilisateur s'authentifie manuellement dans cette fenêtre. Le code ne lit pas la valeur des champs, les cookies, le stockage web ou les mots de passe.
4. Le navigateur vérifie à intervalle régulier uniquement le domaine courant, la présence d'un champ mot de passe, quelques avis d'expiration visibles et des marqueurs sémantiques de déconnexion.
5. Un indicateur de connexion positive est requis avant d'annoncer « Connecté ». Les indices DOM sont centralisés dans `sidjilcom/selectors.py` afin de les adapter si le portail évolue.
6. Après une connexion confirmée, un marqueur local vide est créé dans le profil. Il indique uniquement qu'une session avait déjà été établie et permet de distinguer une demande de reconnexion après redémarrage. Le marqueur seul ne confirme jamais une session active.
7. Les commandes Accueil, Tableau de bord et Trouver une entreprise passent par une file de commandes du thread Playwright. Le tableau de bord/la page métier ne confirment l'accès qu'après vérification du contenu réel; une redirection vers login est rapportée.
8. Le diagnostic ne retourne que l'URL sans query/fragment, le titre, une section reconnue, les liens Sidjilcom connus et les libellés/types des champs visibles. Il ne lit aucune valeur et ne déclenche aucune recherche.
9. La fermeture ferme Chromium et conserve le profil local. Une session expirée ne modifie pas SQLite ni les recherches existantes.

États affichés : `DISCONNECTED`, `CONNECTING`, `WAITING_FOR_LOGIN`, `CONNECTED`, `SESSION_EXPIRED`, `ERROR`; `DISCONNECTING` est un état transitoire de fermeture.

## Configuration

`SessionConfig` impose HTTPS et l'hôte `sidjilcom.cnrc.dz`; il refuse les credentials, paramètres et fragments dans l'URL. Par défaut, Chromium est visible et le profil est placé dans le répertoire de données utilisateur SIDJILY. `SIDJILY_BROWSER_HEADLESS`, `SIDJILY_BROWSER_PROFILE` et `SIDJILY_SIDJILCOM_URL` permettent de configurer ces options sans coder de secrets.

## Sécurité et limites

Le dossier de profil est un secret local au même titre qu'un état de connexion : ne pas le partager ni le committer. `.gitignore` exclut le profil, cookies, fichiers de stockage Playwright, la base et les logs. Les événements de session ne contiennent que les changements d'état; les messages de page, URL courantes, valeurs de formulaire et exceptions complètes ne sont jamais journalisés.

Le détecteur est volontairement prudent : il s'appuie sur un marqueur de session utilisateur ou sur l'accès au contenu d'une route protégée et traite une page de connexion après une session établie comme expirée. Il ne résout ni CAPTCHA ni protection anti-bot. Les textes/attributs du portail peuvent changer; une validation réelle avec un compte autorisé reste nécessaire. Si aucun marqueur positif ni contenu protégé n'est observé, le détecteur reste en attente au lieu d'annoncer une connexion non confirmée.

## Recherches et schéma SQLite

`TaskManager` persiste les recherches, tâches, événements et progression. La tâche 2 ne change pas le schéma SQLite et n'ajoute pas de moteur de recherche ou d'extraction. `PRAGMA user_version` identifie le schéma; toute évolution future devra ajouter une migration versionnée et ne pas recréer la base existante.
