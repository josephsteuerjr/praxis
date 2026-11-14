# Un contrôle accepté n'est pas devenu terminal

Si la liste active montre paused/blocked alors que `events.jsonl` contient un `task_control` accepté, ne pas fermer le manifeste à la main. Vérifier acceptation réelle, génération, checkpoint et suite des événements.

Deux couches diagnostiques historiques :

1. Après exécution, un garde de sortie compare ancien reçu et nouveau brouillon. Une différence peut produire `DurableExecutionError` et `process_recovery` par-dessus le contrôle accepté. Examiner l'ordre de `_land_addressee_free_work_control` et `_prepare_authored_delivery` dans `_AgentResumeRuntime`.
2. Le précontrôle empêche toute exécution. Des `resume_attempt_idle` répétés ne prouvent pas une progression. Examiner `plan_resume` et l'admissibilité de `checkpoint_control` pour un travail sans destinataire, appels en attente ni sortie au checkpoint.

Lire le dernier `run_checkpoint`, `work_loop.control` et le résultat correspondant. Préserver contrôles de bail/curseur et d'intégrité ; une ligne arbitraire du journal n'autorise pas une terminaison.

Vérifier régression ciblée et événements réels `status_changed` / `recap_written` par la voie prise en charge. Analyser correctement le contrôle ne prouve ni fin des enfants ni libération du writer Forge.

Stationnement wait, pause du propriétaire, checkpoint absent et garde de processus sont distincts. Ne pas les fermer en masse ni reprendre une pause explicite. Une fusion historique dans un projet ne prouve pas la présence du correctif dans toute version installée.
