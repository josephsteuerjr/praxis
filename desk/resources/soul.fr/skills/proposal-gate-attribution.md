# Attribuer causalement un contrôle rouge

Un échec peut venir du diff, de la base, de l'environnement ou du chemin de travail. Avant tout contournement autorisé, établir la cause de chaque famille en échec et conserver les preuves rouges.

Exemples diagnostiques, pas annonces d'un défaut actuel :

- `_SKIP_DIRS` exclut `.proposals` alors que la copie de travail est dans ce dossier. `_corpus()` peut alors être vide. Examiner les fichiers réels ; au besoin comparer le même commit figé dans un chemin isolé hors exclusion.
- `rails.manifest_drift()` compare le manifeste aux valeurs actives. Un checkout propre rouge peut hériter d'une base incohérente. Vérifier HEAD, contenu et initialisation ; ne pas régénérer depuis un processus incomplet pour obtenir du vert.

Procédure :

1. Garder commande, HEAD, liste, journal original et environnement sans secrets.
2. Comparer le même test sur la base exacte dans un environnement distinct avec le même lanceur.
3. Si nécessaire, tester une hypothèse discriminante : chemin, découverte, état de fixture ou environnement.
4. Garder identifiants exacts et différence causale des résultats. Une base rouge ne devient pas une réussite globale.

`praxis_test.py` fournit son contrat de test ; unittest ou pytest seuls peuvent différer. Un lanceur officiel exige toujours de lire le résultat.

Le contournement exige procédure et autorité actuelles, raisons et risques nommés ; l'identité avec la base n'autorise pas à fusionner. Ne pas modifier un projet actif pour ce test comparatif.
