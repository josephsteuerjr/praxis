# Travail d'ingénierie dans Forge

Appeler les outils selon leurs schémas actuels fournis ; vérifier les capacités avec `my_capabilities`.

1. Ouvrir `coding_session` avec objectif, cible et isolation adaptée. `target="self"` désigne le code propre de l'application ; `isolation="auto"` choisit une voie disponible. Vérifier racine source, copie de travail et mode renvoyés ; `direct` modifie l'arbre choisi.
2. Lire fichiers, symboles, diff, historique et diagnostics avec `coding_inspect`. Prendre la version dans la lecture.
3. Modifier avec `edit` : fournir `expected_sha256`, examiner le résultat de chaque fichier et relire l'état. `partial` ne promet aucun retour arrière ; `in_doubt` exige d'établir le résultat avant répétition.
4. Utiliser `coding_run` ou `coding_process` persistant ; lire résultat, sortie et journal. Python au premier plan peut appeler les outils via `h` avec contrôle de génération et de possession du processus local. Fournir `task_id` explicitement pour une tâche distante.
5. Employer `coding_agent`, `coding_swarm` et `coding_review` seulement si instructions, pouvoirs et budget actuels le permettent. Définir un résultat et examiner journal et bilan de chaque participant ; contrôle par `coding_control`.
6. Exécuter les contrôles adaptés, lire `coding_verify` et le diff. Terminer après vérification du résultat et conservation des preuves.

Résultat inconnu, garde des processus et résultat de tâche sont distincts. Commencer la récupération par statut, historique et reçus ; ne pas supprimer les verrous ni inventer un état terminal.

Ne pas promettre courtier root ou systemd sur toute machine. Avant un redémarrage autorisé, conserver état et procédure de récupération ; après, vérifier nouvelle génération et réponse vivante. Garder les secrets hors des journaux.
