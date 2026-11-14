# Activité et garde d’un worker Forge

`supervisor.log` vide, ancienne date ou longue réponse modèle ne prouvent
pas un blocage. Journal frais, CPU élevé ou connexion 443 ne prouvent
pas non plus progrès utile ou arrêt sûr.

Interroger l’objet exact par `coding_agent` et les contrôles prévus.
Comparer tâche, worker, génération, hôte, événements et résultat.
Sous Windows, pas de sonde `os.kill(pid, 0)`. Un PID ou un chef disparu
ne décrivent pas tout l’arbre.

Résultat et garde des processus sont distincts. `status=unknown`
n’est ni réussite ni preuve d’activité. Ne pas remplacer
`custody_recovered=false` et `continuation.safe=false` par un stopped
inventé. Avec `next_boundary=summary_in_flight`, ne pas rejouer
automatiquement l’étape faute de résultat final.

Arrêter par `coding_control` et vérifier la fin de l’arbre possédé exact.
Sur le chemin Windows prévu, la preuve dépend d’une liaison Job durable,
pas d’un nom deviné. Ne pas effacer le verrou ni modifier l’état terminal
pour libérer le writer. Garder copie de travail, résultats et histoire.

Les anciennes tâches sans liaison suffisante suivent la récupération
prévue : propriétaire confirmant l’hôte Windows d’origine, puis redémarrage
complet de l’OS si requis. Aucun reboot unilatéral. Relancer l’application
n’est pas cette preuve. Le résultat inconnu le reste même après libération sûre de la garde.
