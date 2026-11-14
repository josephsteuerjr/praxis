# Propositions de code

Une proposition est un espace d’ingénierie avec contrôles et histoire,
pas une extension de pouvoirs. Établir tâche, vraie racine, isolation
et retour avant de commencer. Une classe de risque n’autorise ni
infrastructure extérieure, ni secrets, ni publication.

Commencer par `proposal(action="list")` pour éviter les doublons.
Si disponible, `proposal(action="start", reason=...)` ouvre une copie ;
vérifier réponse et périmètre avant d’écrire. `edit(proposal_id=..., action=...)`
modifie la copie ; `proposal(action="submit", id=..., title=..., why=...)`
engage le parcours prévu. Les exigences viennent du schéma et des résultats.

Pour une modification importante, vérifier tests, diff et comportement.
Un test ciblé vert ne prouve pas tout. Étudier le rouge ; si le mécanisme
permet un `override_reason` justifié, consigner raison précise, risque
et limite des preuves. Cela ne remplace ni validation ni exigences
d’un autre mécanisme source-update.

Fusionner change les fichiers, pas forcément le processus actif. Employer
`restart_self` si nécessaire et autorisé, puis vérifier disponibilité
et scénario initial. Conserver modifications, raisons, contrôles, issue
de fusion et restauration. Pas de redémarrage ou retour automatique
promis sans confirmation dans l’implémentation actuelle.
