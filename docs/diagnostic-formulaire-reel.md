# Diagnostic réel du formulaire Personne morale — Tâche 13

## But et limites

Ce bouton ne lance pas une recherche et ne corrige pas le précontrôle de Tâche 12. Il inspecte uniquement la page déjà ouverte dans le Chromium **SIDJILY**, sans navigation, clic dans la page, saisie/effacement, choix d'une suggestion, soumission ou ouverture de résultats. Les essais automatiques sont simulés; aucun DOM Sidjilcom réel n'a été inspecté par les tests.

Le rapport ne collecte ni n'affiche les valeurs des champs ou des options sensibles, données d'entreprise/personnelles, mot de passe, cookies, session, jeton, réponse réseau ou endpoint/API direct. Les valeurs ne sont utilisées qu'indirectement pour produire un constat booléen/compte « vide/non vide » utile à la règle de préremplissage existante. Les URL/actions sont assainies. Les options ne sont affichées que si le filtre existant les autorise; `nrc1` à `nrc5` sont décrits structurellement, sans interprétation.

## Utilisation sur le PC

1. Mettre à jour/installer la branche SIDJILY, puis lancer l'application comme indiqué dans le README (`sidjily` après installation, ou `python -m sidjily.main`).
2. Cliquer **Ouvrir Sidjilcom**. Effectuer toute authentification manuellement dans le Chromium SIDJILY; ne pas employer un profil Chrome personnel.
3. Dans ce navigateur, laisser ouverte la page Personne morale / « Rechercher par l'information de la société » que vous voulez diagnostiquer. Le bouton de diagnostic ne navigue pas vers cette page et ne sélectionne pas le mode.
4. Dans **Capture à comparer**, choisir l'étape correspondant à l'état qui est déjà affiché :
   - **Après navigation** : juste après l'arrivée sur le formulaire;
   - **Après sélection de la Wilaya** : uniquement si cette sélection a déjà été faite dans le cadre normal de votre activité;
   - **Juste avant soumission (sans soumettre)** : lorsque l'état est déjà prêt à être vérifié. **Ne cliquez pas sur Rechercher.**
5. Cliquer exactement **Diagnostiquer le formulaire réel**. Pour une comparaison, relancer explicitement le bouton à chaque étape disponible, sur l'état courant du navigateur. Le diagnostic ne crée pas ces états et ne choisit aucune suggestion. Si un état n'existe pas, ne le provoquez pas pour ce test : les captures restantes restent utilisables.
6. Lire le rapport dans la zone de texte, puis utiliser **Copier le diagnostic** ou **Enregistrer le diagnostic**. Le rapport compare chaque capture à la précédente dans la même session Chromium SIDJILY; fermer/recréer cette session efface cette mémoire temporaire.

## Lire les constats

Le rapport reproduit séparément, sans modifier leur logique, les vérifications de Tâche 12 : route officielle, mode sélectionné et marqueur URL/titre stable, unique formulaire correspondant au suffixe `_wilcom`, méthode `POST` et action officielle, contrôle attendu visible/activé/vide/conforme au mapping, vacuité des autres contrôles visibles, et exactement un bouton **Rechercher** visible/activé associé au formulaire.

Les constats sont ordonnés par priorité de diagnostic :

- **ABSENT / route** : aucun `<form>` visible ou page/route inattendue;
- **CHAMP ABSENT / ASSOCIATION** : suffixe `_wilcom` non recensé, ou contrôle trouvé hors du formulaire que le précontrôle examine;
- **AMBIGUÏTÉ** : plusieurs formulaires ou contrôles correspondants;
- **PRÉREMPLI** : au moins un contrôle utilisateur visible non vide; seules les identités structurelles nécessaires et le compte sont rapportés, jamais la valeur;
- **MÉTHODE / ACTION / BOUTON** : divergence avec les conditions actuelles;
- **COMPARAISON** : ajouts/retraits de champs, formulaires, boutons ou changement du compte booléen de vacuité entre captures, pouvant indiquer un DOM remanié/dynamique.

Si la capture actuelle satisfait les conditions mais que l'échec passé ne se reproduit pas, le rapport ne peut pas reconstituer le marqueur historique URL/titre ni un état DOM passé. Il faut alors comparer les captures faites à chaque état; aucune cause réelle n'est confirmée par les tests simulés.

Les 14 lignes de référence décrivent le mapping personne morale (7 champs communs + 7 champs spécifiques). Le mapping central nomme actuellement l'associé en un contrôle combiné `_nom_pr`; le rapport affiche aussi les contrôles et labels effectivement observés, y compris si Nom et Prénom sont séparés dans le DOM. Les composants `nrc1` à `nrc5` restent sans signification attribuée.
