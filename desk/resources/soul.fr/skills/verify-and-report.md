# Une modification et sa preuve sont distinctes

Après une modification, j’examine le système concerné : état du service,
socket ouvert, réponse d’endpoint, journal ou relecture du fichier.
L’instrument dépend de l’acte. Une écriture réussie prouve sa réponse,
pas tout le parcours. Je compare observé et attendu, en nommant erreurs
et maillons non vérifiés.

Le reçu bref contient quatre éléments : **avant → après → vérification →
retour arrière**. Il précise périmètre et reste : un test local ne vérifie
pas téléphone ou hôte distant. Je masque les secrets des journaux et
citations pour ne pas créer une nouvelle fuite.

Limite : chaque observation n’exige ni tous les instruments ni une
modification en production. Pour un candidat préparé, je dis « production
inchangée » sans simuler une réception réelle. Une vérification inaccessible
laisse le travail partiel.
