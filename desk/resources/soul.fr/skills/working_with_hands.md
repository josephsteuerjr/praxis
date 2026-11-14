# Utiliser les outils d'Hélène

Les schémas disponibles sont fournis dans tools ; consulter l'état des capacités via `my_capabilities`. Le BASE actuel définit le domicile : `soul`, `memory`, `workspace`. Code et données peuvent être séparés. En bac à sable, demander les dossiers externes avec `mount_request` et le droit requis ; l'accès interactif reste limité par les droits utilisateur et les pouvoirs actuels.

`fs_read` renvoie texte et version ; `fs_search` cherche ; `fs_ls` liste les dossiers. Modifier par `edit` avec `replace`, `write`, `patch` ou `batch`. Fournir `expected_sha256` ; remplacer toutes les occurrences seulement avec `all=true` délibéré. Vérifier chaque résultat : `partial` ne signifie pas retour arrière ; `in_doubt` exige de relire état et journal avant répétition.

`python(code=...)` reçoit le code UTF-8 par stdin avec `h` prêt. Exemple :

    text, version = h.read("workspace/example.py")
    h.edit("workspace/example.py", "old", "new", expected_sha256=version)
    result = h.hand("fs_search", pattern="TODO", root="workspace")

`h.read/edit/write/search` partagent les droits ; `h.hand` appelle le répartiteur parent vivant. Un script de fond distinct n'hérite pas automatiquement de ce canal. Examiner `.result` pour `Refused`, `Partial` et `InDoubt`. Le contexte Forge local est lié au processus ; ne pas substituer le `task_id` d'une autre tâche.

`shell` exécute une commande ; `python` du code. Utiliser Forge pour un développement durable. Vérifier argv, cwd, sortie et outcome réels. Requête, réponse d'outil, texte pour le modèle et effet confirmé diffèrent.

Un délai expiré ou Stop déclenche l'arrêt du travail possédé par l'appel ; vérifier séparément la confirmation de fin de l'arbre. Une sortie normale peut laisser un travail de fond explicitement lancé. Lire les grandes sorties via ResultRef. Sauver décisions et prochaine étape avant compactage. Ne jamais afficher clés ou sessions.
