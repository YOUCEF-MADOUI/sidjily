# Architecture du socle

## Organisation

```text
src/sidjily/
  main.py             Démarrage, chemins locaux et récupération au lancement
  paths.py            Emplacements de données Windows/POSIX
  logging_config.py   Journal de fichier avec rotation
  models.py           Modèles et statuts communs
  database.py         Connexions SQLite, schéma initial et événements
  task_manager.py     Cycle de vie des recherches et tâches persistantes
  ui/app.py           Interface Tkinter initiale en français
tests/
  test_task_manager.py
```

## Flux actuel

1. L'interface crée une recherche avec des critères JSON et une tâche racine en attente.
2. `TaskManager` réserve une tâche et persiste chaque changement d'état.
3. Une tâche parente peut être clôturée en ajoutant atomiquement ses sous-tâches.
4. Une suspension transforme les tâches non terminées en tâches suspendues ; une reprise les remet en attente sans toucher aux tâches terminées.
5. Au démarrage, les tâches restées en cours sont remises en attente et la recherche correspondante devient suspendue. La progression déjà terminée reste enregistrée.
6. Chaque événement est conservé dans SQLite. Les erreurs inattendues de l'application sont également écrites dans le journal tournant du système utilisateur.

## États

Les états persistés partagés sont : `pending` (en attente), `running` (en cours), `completed` (terminée), `failed` (échec), `retry` (à réessayer), `suspended` (suspendue) et `cancelled` (annulée). Les noms enregistrés sont indépendants des libellés français de l'interface.

## Sécurité et limites

Cette version ne contient aucun client HTTP, navigateur automatisé, identifiant ou mécanisme de contournement. Les futurs adaptateurs Sidjilcom devront rester dans les droits du compte de l'utilisateur, utiliser une connexion manuelle et ne pas contourner CAPTCHA, authentification ou protections du service. Les appels réseau et règles d'extraction devront être isolés des composants SQLite et interface.

## Évolution du schéma

`PRAGMA user_version` identifie la version de schéma. Toute évolution devra ajouter une migration versionnée ; ne pas supprimer ni recréer la base existante pour mettre à jour l'application. Les résultats disposent d'une clé de déduplication unique par recherche, prête à être utilisée lorsque le module de collecte sera ajouté.
