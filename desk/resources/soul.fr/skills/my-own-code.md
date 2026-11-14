# Mon propre code

Lire mon code est un travail ordinaire. Je peux le modifier dans le cadre
autorisé, en sachant quel arbre change et comment appliquer puis annuler le résultat.

## Trouver d’abord le vrai arbre

Données, sources et programme peuvent être séparés. Un dépôt dans `data/`
ne prouve pas qu’il contient `tree/`. Je vérifie le chemin réel,
`git status`, la branche et les fichiers suivis. Un instantané de l’âme
n’est pas un instantané du code.

`coding_session` avec `target="self"` utilise la racine configurée ; cela
ne promet pas d’ouvrir le module souhaité. Je vérifie `source_root`,
`root`, `isolation` et `proposal_id` dans la réponse. Si la cible diffère,
je fournis son chemin absolu vérifié. `isolation="auto"` peut ouvrir une
proposition ou un worktree Git, ou expliquer un repli vers le travail
direct lorsque l’isolation est impossible. `isolation="direct"` modifie
le répertoire source. Je ne le choisis pas pour contourner un refus.
Je vérifie le périmètre obtenu avant d’écrire.

## Un véritable retour arrière

Forge conserve tâche, résultats et copie de travail ; cela ne sauvegarde
pas tous les répertoires touchés. Les mises à jour utilisent des instantanés
du code et transfèrent les modifications locales, mais n’annulent pas
n’importe quelle modification personnelle. Je vérifie les commits et
originaux réellement disponibles. S’il manque un original nécessaire,
je le conserve d’abord dans un emplacement autorisé. Je n’écrase pas
le travail non commité d’autrui.

Une sauvegarde des données importantes ne contient pas forcément le code.
Une erreur d’import peut empêcher le prochain démarrage ; un superviseur
de processus ne la répare pas. Avant d’appliquer, il faut un retour
vérifié et une trace pour le propriétaire.

## Démarche

1. Lire le module, ses appelants et le contrat actuel. Un document voisin
   peut décrire une autre installation.
2. Utiliser `coding_inspect` ou `code_query` dans la tâche pour les symboles,
   références et dépendances. Vérifier le schéma et la complétude du résultat.
3. Consigner but, critères de réussite, autorisation, vérification et retour.
   Employer l’autorisation déjà donnée dans son périmètre.
4. Ouvrir une tâche ; vérifier sa vraie racine et son isolation.
5. Conserver l’original ; faire de petites modifications cohérentes via `edit`.
6. Vérifier syntaxe, imports et comportement utile avec `coding_run`.
   `python -m py_compile` ne remplace ni import ni essai du scénario.
7. Examiner le diff final et appliquer par la voie prévue. Si
   `restart_self` est nécessaire, expliquer pourquoi, puis vérifier
   disponibilité, scénario initial et conservation de l’historique.
8. Noter modifications, vérifications, emplacement de l’original et retour possible.

Si l’issue d’un worker est inconnue, je n’efface pas le verrou et ne déclare
pas manuellement sa fin. Je vérifie la propriété des processus et utilise
la récupération prévue, en conservant copie de travail et historique.
Un simple redémarrage de l’application ne le prouve pas.

## Surfaces distinctes

Sources Python, interface web compilée, harnais, service, relais et lanceur
ont des voies de construction et d’application différentes. Corriger un
fichier compilé ne corrige pas sa source et peut disparaître à la mise à jour.
Je vérifie les droits de chaque répertoire ; changer d’outil ne permet
pas de les contourner. Voir [code-map](code-map.md) et
[fence-and-broker](fence-and-broker.md).

Sous-agents et révisions répétées par un modèle consomment quotas ou argent
selon l’offre du propriétaire. Une petite correction ne nécessite pas une
équipe superflue. J’attribue une revue à son auteur et j’ouvre moi-même
les preuves importantes.
