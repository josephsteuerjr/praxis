# Tampon de sortie des calculs longs

Un journal vide ne prouve pas un blocage : stdout vers un fichier peut
être tamponné. CPU élevé et mémoire croissante ne prouvent pas non plus
un progrès utile : boucle ou fuite sont possibles. Vérifier appartenance,
temps, compteurs, résultats et erreurs.

Employer une opération gérée avec identifiant et journal. Sur POSIX
compatible, `stdbuf -oL` peut rendre la sortie ligne par ligne ; vérifier
présence et effet. Dans mon code C, `setvbuf` ou `fflush(stdout)` explicite
peuvent aider. Ce n’est ni universel ni une commande Windows.

Le tampon actif peut être inaccessible de l’extérieur. Ne supposer ni
droits de débogage, ni ptrace, ni changement sysctl du conteneur. Avant
redémarrage, évaluer perte, autorisation et checkpoint ; garder preuves,
arrêter seulement mon arbre confirmé et vérifier la fin. Journal vide
ou seul PID vivant ne suffisent pas.
