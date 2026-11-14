# Vérification préalable d'une tâche importante

À employer avant modification d'état durable, contrat public, frontière externe, plusieurs composants/domaines de confiance, migration coûteuse ou action difficilement réversible. Sans solution choisie, commencer par une recherche bornée. Ne pas ritualiser une petite modification réversible. Accords et pouvoirs existants persistent ; ce contrôle n'en crée pas.

## Un état de référence actuel

Maintenir une source actualisée dans les checkpoints/notes Forge ou un artefact explicitement choisi. Les transmissions y renvoient sans créer de copies divergentes.

Au départ : objectif, critère de fin, `out of scope` important, priorités et pouvoirs, plan, état observé, limites et raison du contrôle. Ajouter preuves, décisions motivées, étape actuelle/suivante, inconnues et options rejetées. Terminer par le résultat réel.

Mettre à jour avant une étape coûteuse ou ramifiée. Après interruption, lire transmission et instructions, puis comparer à l'arbre, au diff et aux résultats réels. La mémoire oriente sans remplacer la vérification. Un artefact seulement dans le chat ne termine pas la transmission si un lieu canonique a été désigné.

## Questions centrales

1. Coût et priorité : sécurité, intégrité, observabilité, fonctions, vitesse, compatibilité et migration. Gravité et irréversibilité se distinguent de la fréquence. Identifier la priorité existante qui tranche.
2. Échelle causale : classe ou cas isolé ? Que cacherait un correctif local, que gonflerait une couche générale ? Noter la classe, l'échelle choisie et le mode de défaillance précis de l'alternative.
3. Solution existante : la chercher dans le projet et vérifier code, état, documentation ou expérience. Des noms proches ne prouvent pas l'adéquation.
4. Premier échec et récupération : comment détecter, arrêter et réparer le dommage ? Avant migration ou action peu réversible, nommer sauvegarde ou impossibilité, essai/déploiement progressif, signal d'arrêt, dernière annulation et récupération. Préférer un retour testé ; sinon confinement, impact borné et récupération en avant vérifiable. Sans récupération ni confinement, `NO-GO` ou décision exceptionnelle autorisée aux conséquences explicites.
5. Observabilité : distinguer réussite, absence d'action acceptable et panne sans lire le code. Quel contrôle économique départage les hypothèses ? Quels contrôles avant la fin ? Lire un artefact existant suffit souvent.

## Seulement les modules pertinents

- Comportement déduit : signal observable, erreur dangereuse et test discriminant ; faux positifs et négatifs séparés.
- Frontière déclarée : source d'autorité, portée et contrôle exécutable. Ne pas fondre pouvoirs, données, ressources et sécurité dans un drapeau anonyme.
- Fusion/séparation d'entités : nommer la distinction causale perdue ou le doublon créé.
- Limite de capacité : restrictions connues et test négatif/silence attendu si l'élargissement involontaire est dangereux.

## Décision

- `GO` : hypothèses décisives suffisamment étayées ; l'incertitude résiduelle ne change pas échelle, échec acceptable ou récupération.
- `GO WITH RESIDUAL RISK` : risque nommé, borné et observable si possible ; conséquences et limites de connaissance acceptées par l'autorité du projet.
- `NO-GO` : méthode rejetée, avec raison, preuves et condition de réexamen.
- `BLOCKED` : fait/capacité manquant et condition de déblocage identifiés.
- `ESCALATE` : question, décideur autorisé et insuffisance de pouvoirs/preuves identifiés.

L'admission ne permet que le plan examiné et ne prouve pas le succès. Réexaminer si diff/périmètre changent, nouvelle frontière, hypothèse défaillante, impact accru ou récupération modifiée. Nommer l'inconnu n'autorise pas l'irréversible. Un plan inchangé confirmé par les preuves est valable.

Par itération, choisir le contrôle discriminant économique le plus proche et préciser avant son effet sur la décision. Mettre ensuite le dossier à jour. Plusieurs tours sans conclusion exigent de borner la recherche, nommer le fait manquant ou escalader ; trois est une heuristique, pas un motif d'abandon du travail autorisé.

Un second passage indépendant aide pour migration, irréversibilité et domaines multiples si instructions et budget le permettent. Donner un paquet neutre sans imposer d'hypothèse ; comparer des différences concrètes, pas des verdicts génériques. Ne pas invoquer un sous-agent contre une interdiction. Dire honnêtement si la revue manque : dommage borné et récupération vérifiée peuvent laisser un risque résiduel ; conflit d'autorité ou dommage irréversible non observable exigent blocage/escalade.

Le dossier garde état initial, décision, différence du plan, preuves, résultats des contrôles choisis, récupération et inconnues. Établir les classes récurrentes par cas indépendants comparables, pas par un nombre magique.

Origine : `CHECKLIST.md` d'Ivan dans `konradkarso-droid/agent-mobile`. Édition portable adaptée, pas copie synchronisée automatiquement.
