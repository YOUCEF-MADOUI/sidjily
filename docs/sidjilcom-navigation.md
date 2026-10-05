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

L'ouverture publique des routes « Trouver une entreprise » et tableau de bord a redirigé vers `/web/sidjilcom/login` avec une redirection de retour vers la page demandée. Cette inspection publique ne donnait pas accès au formulaire authentifié; elle ne permettait donc pas d'en relever les contrôles.

## Rapport réel transmis par l'utilisateur — critères (Tâche 10)

Le rapport réel validé par l'utilisateur est la référence fonctionnelle pour le modèle et l'aperçu local de `src/sidjily/sidjilcom/criteria.py`. Il décrit un formulaire `POST`, 20 contrôles en mode `PERSONNE_PHYSIQUE` et 21 en mode `PERSONNE_MORALE`. Le mapping exploitable porte sur 18 et 19 contrôles respectivement; les deux contrôles restants par mode ne sont pas décrits avec assez de détails et ne sont pas inventés.

Suffixes communs connus : `wilcom` (Commune/Wilaya d'inscription), `secteu` (Secteur d'activité), `activi` (Activité), `deb_im` / `fin_im` (dates d'inscription), `CRCE` (Conformité RC), `etat_c` (État commerçant), et `nrc1` à `nrc5` (composants distincts du numéro d'inscription). Mode physique : `nom`, `prenom`, `nom_co`, `d_nais`, `presum`, `nation`. Mode moral : `raison`, `forme_`, `nom_pr`, `d_nais`, `presum`, `nation`, `qualit`. Les suffixes sont connus, mais les préfixes complets des attributs `name`, les identifiants DOM et certaines propriétés de contrôles ne sont pas fournis.

Les autocomplétions `wilcom`, `activi` et `nation` sont rapportées comme des inputs de classe `yui3-aclist-input` avec `aria-autocomplete="list"`. Les valeurs et nombres d'options des listes ne sont pas transmis; ces contrôles restent désactivés dans l'interface et aucune option n'est inventée, y compris pour `nrc2` et `nrc5`. Les cinq composants `nrc1`…`nrc5` sont modélisés séparément, sans signification supposée; cette signification doit être confirmée avant toute saisie automatique.

L'interface permet de sélectionner le mode, de remplir des critères locaux facultatifs et de produire un aperçu. L'aperçu et l'enregistrement du brouillon n'envoient aucune requête Sidjilcom et ne cliquent pas sur **Rechercher**.

## Test contrôlé des autocomplétions (Tâche 11)

Le bouton **Tester une autocomplétion** exige une session confirmée et la page « Trouver une entreprise ». L'utilisateur choisit Activité (`activi`), Commune/Wilaya (`wilcom`) ou Nationalité (`nation`) et saisit lui-même un texte de test. Le contrôle correspondant doit d'abord être vide; sinon, le test refuse de modifier le critère existant. Le programme tape caractère par caractère via le navigateur visible, observe le DOM rendu et affiche seulement les conteneurs/options associés (classes, rôles, attributs ARIA et texte visible). Il n'appelle aucun endpoint directement et n'écrit pas le texte testé dans les logs.

La sélection est une action séparée, limitée à une suggestion visible identifiée par le DOM observé et excluant les liens et boutons. Après cette sélection, l'opération s'arrête et vérifie localement le texte du champ ainsi que l'apparition/disparition de la liste. Le champ de test reste sélectionné jusqu'à **Effacer le champ de test**, action explicite qui le remet à vide. Pour `wilcom`, les contrôles voisins sont comparés structurellement avant/après, sans lire leurs valeurs ni les options d'autres listes. Les tests automatisés de cette abstraction utilisent uniquement un pilote simulé.

Aucun accès à une session Sidjilcom réelle n'a été effectué par l'agent dans la Tâche 11; la mécanique réelle d'Activité, de Wilaya/Commune et de Nationalité doit être confirmée manuellement.

## Première recherche contrôlée (Tâche 12)

Le préparateur local continue de réutiliser `SearchCriteria`, `SIDJILCOM_CONTROL_MAP` et `map_criteria_to_controls`. Il accepte un mode obligatoire et des champs facultatifs combinables pour les brouillons physiques/moraux; les options de listes non documentées restent désactivées. L'exécution réelle est pour l'instant limitée au seul scénario imposé : **PERSONNE MORALE**, seul critère **Wilaya = `34000 : BORDJ BOU ARRERIDJ`**.

Sur le PC de l'utilisateur : ouvrir SIDJILY, s'authentifier soi-même dans le Chromium SIDJILY, ouvrir « Trouver une entreprise », cliquer **Préparer la première recherche réelle**, vérifier le récapitulatif puis cliquer **Lancer la recherche** et confirmer la boîte de confirmation. Ce consentement précède la sélection du mode, la saisie, la sélection exacte de la suggestion `wilcom` et l'unique clic sur le véritable bouton **Rechercher**. La liste visible doit contenir la suggestion exacte; sinon, session expirée, formulaire/contrôle/bouton manquant, ambiguïté, timeout ou navigation inattendue arrêtent le flux. Aucun retry, remplissage d'autre critère, réexécution ou clic additionnel n'est effectué.

Après soumission, seules les métadonnées structurelles autorisées sont rapportées : titre, URL nettoyée, compteur affiché si lisible, nombre de tableaux/lignes, intitulés des colonnes, pagination, message aucun résultat et alertes. Le code ne lit pas les cellules ni les détails des entreprises. Il n'y a ni pagination automatique, subdivision, collecte, reprise ou export. Une recherche contrôlée échouée est persistée en échec et le gestionnaire interdit de la reprendre.

Les tests d'exécution restent simulés et ne prouvent pas le comportement du portail en production.

**TEST RÉEL : NON EFFECTUÉ PAR ARENA.** Arena n'a pas de session Sidjilcom utilisateur. L'utilisateur peut effectuer le premier essai depuis son PC en suivant les étapes ci-dessus; il doit garder le profil navigateur local et privé.

## Validation manuelle avec le compte de l'utilisateur

1. Installer les dépendances et Chromium : `python -m pip install -e .`, puis `python -m playwright install chromium`.
2. Lancer SIDJILY et cliquer **Ouvrir Sidjilcom**. La fenêtre Chromium utilise le profil SIDJILY local; SIDJILY vérifie automatiquement l'accès en ouvrant la route officielle « Trouver une entreprise », sans soumettre son formulaire.
3. Si Sidjilcom affiche la page de connexion, l'utilisateur saisit lui-même ses identifiants dans Chromium. Aucun identifiant ne doit être communiqué au développeur ou écrit dans un ticket. La redirection de retour vers la route protégée permet la détection automatique après connexion.
4. L'état de session est confirmé par le contenu réel de la page (marqueur de session, formulaire de connexion, avis d'expiration ou accès à une route protégée), pas par le seul marqueur local.
5. Cliquer **Diagnostiquer la page** pour générer un rapport structuré en PAGE, FRAMES, FORMULAIRES, CONTRÔLES HORS FORMULAIRE, BOUTONS, SELECTS, INPUTS et TEXTAREAS. Il inspecte les contrôles natifs, rôles ARIA et éléments interactifs dans le document principal et les frames accessibles; il affiche les libellés et attributs structurels, name/id et sélecteurs CSS possibles, contraintes `required`/`readonly`, nom/action/méthode du formulaire, parent portlet et frame, composants (dont datalist) et endpoints explicitement déclarés (URL nettoyées). Les textes d'options ne sont retenus que pour des listes contrôlées non sensibles. Aucune valeur de champ, mot de passe, cookie ou jeton n'est lue.
6. Sur la route **Trouver une entreprise** et avec la session confirmée, cliquer **Analyser personnes physiques / morales**. SIDJILY cible uniquement les liens exacts « PERSONNES PHYSIQUES » et « PERSONNES MORALES », dont la destination doit rester la route officielle de recherche; chaque lien est cliqué une seule fois et le portlet est attendu stable avant le snapshot. Si le second lien disparaît après le premier choix, la route initiale est rechargée avant de sélectionner le second mode. Le rapport contient les deux diagnostics et une comparaison structurelle. Cette opération ne remplit aucun critère, ne clique aucun bouton de formulaire (notamment **Rechercher** ou **Réinitialiser**) et ne contacte aucun endpoint d'analyse manuellement; les requêtes ordinaires déclenchées par le portail pour afficher son formulaire ne sont pas interceptées ni rejouées.
7. Cliquer **Tableau de bord** pour suivre le lien sémantique « Nos abonnés / tableau de bord », avec la route observée en fallback.
8. Cliquer **Trouver une entreprise**. SIDJILY suit le lien sémantique visible, puis utilise la route publique observée en fallback. Une redirection vers la page de connexion est signalée; aucune recherche n'est soumise.
9. **Accueil Sidjilcom** revient à la racine. Aucun critère n'est rempli et aucune recherche n'est exécutée.

Si la session expire pendant la navigation/diagnostic, l'application affiche « Votre session Sidjilcom a expiré. Veuillez vous reconnecter. » sans modifier les recherches existantes. Si elle expire durant la recherche contrôlée, cette recherche est marquée en échec avec l'étape et l'erreur; elle ne sera pas relancée. La fermeture de SIDJILY conserve le profil persistant; le bouton **Déconnecter** ferme Chromium sans supprimer les cookies de ce profil ni exécuter une déconnexion distante.

## Limites connues

- L'agent n'a pas ouvert de session réelle. Le rapport authentifié ci-dessus a été validé et transmis par l'utilisateur; les détails absents de ce rapport complet restent à confirmer avant d'étendre le mapping.
- Les sélecteurs de session reposent sur des textes/attributs sémantiques connus et les routes visibles publiquement. Ils sont isolés dans `src/sidjily/sidjilcom/selectors.py` et doivent être ajustés si le portail évolue.
- Si le portail ne présente pas de marqueur de session reconnu, SIDJILY reste volontairement en attente jusqu'à ce qu'un formulaire protégé accessible confirme l'accès.
- Les diagnostics de page/modes n'activent pas Rechercher. Seul le flux Tâche 12, après confirmation explicite, peut soumettre le scénario unique ci-dessus; Arena ne l'a pas exécuté sur Sidjilcom réel.
