# À quel événement appartient cette heure ?

Avant « hier », « récemment » ou « à l’instant », ouvrir la source de
l’événement exact. `read_log`, `recent_turns` et `fs_ls` peuvent dater
message, réponse, écriture ou métadonnées. Ne pas les substituer.

Comparer message_id, chemin, ligne et fuseau. Pas d’heure voisine
ressemblante. Convertir UTC selon les règles du fuseau actuel, pas
un +4 permanent. Copie ou mtime ne datent pas forcément le contenu.

Une durée précise entre mauvais événements reste fausse. Sans lien
correct, nommer la limite ; mémoire de séquence n’est pas date exacte.
« Vérifié maintenant » exige une vraie lecture, pas un ancien résultat raconté.
