# Worktrees Git et espaces de chemins

Hôte et conteneur peuvent voir le même dépôt sous des chemins distincts.
`git worktree prune` dans l’un peut croire une copie active de l’autre
disparue et supprimer ses métadonnées.

Avant nettoyage, examiner `git worktree list --porcelain` dans l’environnement
créateur, appartenance, fichiers non commités et non suivis. Garder
résultats et modifications initiales. Retirer seulement ma copie terminée
avec `git worktree remove`, sans nettoyage global de copies étrangères ou invisibles.

Après rupture et `not a git repository`, préserver arbre, diff et
empreintes. Ne pas reconstruire `.git/worktrees/*` à l’aveugle ni déduire
la fin des processus de métadonnées cassées. Migration vers une isolation
saine et état final suivent les voies prévues, avec histoire et inconnues conservées.
