# Validation et navigation Sidjilcom

## Éléments publics observés

Inspection sans compte ni soumission de formulaire, le 4 octobre 2026 :

| Libellé visible sur l'accueil | Route relevée |
|---|---|
| Trouver une entreprise | `/fr/group/sidjilcom/repertoire-des-commercants` |
| Recherche Commerçant Ambulant | `/fr/web/sidjilcom/rechercheambulant` |
| Nomenclature de vos activités | `/fr/web/sidjilcom/nomenclature-de-vos-activites` |
| Nos abonnés / tableau de bord | `/group/sidjilcom/mon-tableau-de-bord` |
| Accueil | `/` |

L'ouverture publique des routes « Trouver une entreprise » et tableau de bord a redirigé vers `/web/sidjilcom/login` avec une redirection de retour vers la page demandée. Le formulaire de recherche et le tableau de bord eux-mêmes n'étaient donc pas consultables sans authentification. Les champs métier (numéro d'inscription, raison sociale, activité, wilaya/commune, période, dirigeant, etc.) **ne sont pas déclarés comme observés** à ce stade.

## Validation manuelle avec le compte de l'utilisateur

1. Installer les dépendances et Chromium : `python -m pip install -e .`, puis `python -m playwright install chromium`.
2. Lancer SIDJILY et cliquer **Ouvrir Sidjilcom**. La fenêtre Chromium utilise le profil SIDJILY local; SIDJILY vérifie automatiquement l'accès en ouvrant la route officielle « Trouver une entreprise », sans soumettre son formulaire.
3. Si Sidjilcom affiche la page de connexion, l'utilisateur saisit lui-même ses identifiants dans Chromium. Aucun identifiant ne doit être communiqué au développeur ou écrit dans un ticket. La redirection de retour vers la route protégée permet la détection automatique après connexion.
4. L'état de session est confirmé par le contenu réel de la page (marqueur de session, formulaire de connexion, avis d'expiration ou accès à une route protégée), pas par le seul marqueur local.
5. Cliquer **Diagnostiquer la page** pour générer un rapport structuré en PAGE, FRAMES, FORMULAIRES, CONTRÔLES HORS FORMULAIRE, BOUTONS, SELECTS, INPUTS et TEXTAREAS. Il inspecte les contrôles natifs, rôles ARIA et éléments interactifs dans le document principal et les frames accessibles; il affiche les libellés, attributs, champs obligatoires, action/méthode des formulaires, composants et endpoints explicitement déclarés (URL nettoyées). Les textes d'options ne sont retenus que pour des listes contrôlées non sensibles. Aucune valeur de champ, mot de passe, cookie ou jeton n'est lue.
6. Sur la route **Trouver une entreprise** et avec la session confirmée, cliquer **Analyser personnes physiques / morales**. SIDJILY cible uniquement les liens exacts « PERSONNES PHYSIQUES » et « PERSONNES MORALES », dont la destination doit rester la route officielle de recherche; chaque lien est cliqué une seule fois et le portlet est attendu stable avant le snapshot. Si le second lien disparaît après le premier choix, la route initiale est rechargée avant de sélectionner le second mode. Le rapport contient les deux diagnostics et une comparaison structurelle. Cette opération ne remplit aucun critère, ne clique aucun bouton de formulaire (notamment **Rechercher** ou **Réinitialiser**) et ne contacte aucun endpoint d'analyse manuellement; les requêtes ordinaires déclenchées par le portail pour afficher son formulaire ne sont pas interceptées ni rejouées.
7. Cliquer **Tableau de bord** pour suivre le lien sémantique « Nos abonnés / tableau de bord », avec la route observée en fallback.
8. Cliquer **Trouver une entreprise**. SIDJILY suit le lien sémantique visible, puis utilise la route publique observée en fallback. Une redirection vers la page de connexion est signalée; aucune recherche n'est soumise.
9. **Accueil Sidjilcom** revient à la racine. Aucun critère n'est rempli et aucune recherche n'est exécutée.

Si la session expire, l'application affiche « Votre session Sidjilcom a expiré. Veuillez vous reconnecter. » La base et les tâches sont inchangées. La fermeture de SIDJILY conserve le profil persistant; le bouton **Déconnecter** ferme Chromium sans supprimer les cookies de ce profil ni exécuter une déconnexion distante.

## Limites connues

- Aucune connexion utilisateur réelle n'a été effectuée durant le développement; l'accès au formulaire et ses libellés restent à confirmer manuellement.
- Les sélecteurs de session reposent sur des textes/attributs sémantiques connus et les routes visibles publiquement. Ils sont isolés dans `src/sidjily/sidjilcom/selectors.py` et doivent être ajustés si le portail évolue.
- Si le portail ne présente pas de marqueur de session reconnu, SIDJILY reste volontairement en attente jusqu'à ce qu'un formulaire protégé accessible confirme l'accès.
- La tâche ne remplit aucun champ, ne déclenche aucune recherche et ne collecte aucun résultat.
