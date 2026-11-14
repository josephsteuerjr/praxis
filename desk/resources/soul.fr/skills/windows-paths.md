# Un chemin Windows reste un argument, même avec espaces

Avant commande, enregistrement de service ou configuration, vérifier
le chemin réellement reçu. Dans une chaîne de commande, citer tout
le chemin contenant des espaces. Avec une API de tableau d’arguments,
le transmettre en un seul argument, sans fabriquer une chaîne shell.
Attention particulière à l’exécutable et aux arguments du service :
l’erreur de découpage survit au tour actuel.

Ne pas créer de fichiers auxiliaires à la racine du disque. Après
un script, vérifier sorties réelles et éventuel fichier fantôme préfixé
`C:\Program`, sans prendre le dossier normal `C:\Program Files` pour
un déchet ni supprimer par seul motif générique. Erreur 193 / `0x800700C1`
(« application Win32 non valide ») justifie de vérifier découpage du
chemin et fichier fantôme avant d’accuser le binaire.

Limite : une API d’arguments n’exige pas de guillemets supplémentaires
pour un chemin sans espaces. Confirmer la cause par inspection,
sans traiter l’erreur 193 par suppression aveugle.
