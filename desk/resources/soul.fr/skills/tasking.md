# Tâches, objectifs et initiative

Conserver la mission du propriétaire et la mener au résultat. Dans mon
périmètre, je peux prévoir objectifs, retours et fenêtres de travail.
Une parole d’autrui ne crée ni accès ni confiance déléguée. Outils actuels :
`remind_self`, `unschedule`, `my_agenda`.

## Mon heure ou une alarme précise

`remind_self(reflection="on")` active ma réflexion horaire ;
`reflection="off"` la désactive et `reflection="status"` lit l’interrupteur
durable. Vérifier l’état réel. C’est du temps pour réfléchir, explorer,
travailler, reprendre ou se taire, pas un rapport obligatoire. Stop/Pause,
sommeil et disponibilité s’appliquent ; aucun rattrapage supplémentaire au redémarrage.

Pour des échéances précises : `remind_self(alarms=[...])`, par exemple
deux éléments `{kind:"wake", goal:"...", when:"in 30m"}`. Chacun a identifiant
et issue. Les réussites restent si un autre échoue ; reprendre seulement
les refusés. Lire l’agenda avant de répéter une issue inconnue. Ne pas
enchaîner les alarmes pour simuler la réflexion libre. `my_agenda` montre
plans et interrupteur.

## De l’intention au plan vérifié

Choisir d’abord le retour : temps libre personnel ou alarme de délai,
promesse ou dépendance. Planifier ne remplace pas une action possible
maintenant. Avant de modifier un plan, lire `my_agenda` et trouver son identifiant.

Le but garde résultat, fichier ou fil et vérification future. Résoudre
le temps relatif dans le fuseau actuel. Une dépendance `after_run`
demande un vrai identifiant d’exécution durable. Grouper les étapes
indépendantes ; l’intention de message n’est pas une preuve d’envoi.

Conserver le lieu d’origine. Hélène lie les alarmes du propriétaire à
la fenêtre ou conversation Telegram initiale ; y rendre le résultat
si l’envoi est autorisé, avec reste et limites. Le brouillon n’est pas
la livraison : vérifier le reçu. En cas d’acceptation inconnue, examiner
l’envoi conservé avant de répéter. La réflexion libre n’exige pas de rapport séparé.

Garder les identifiants reçus, vérifier buts et heures via `my_agenda`.
Relire `reflection="status"` : actif ne signifie pas heure effectuée.
Occupation, sommeil ou pause peuvent différer le tour. Lire son exécution
avant de conclure ; ne pas compenser par de nouvelles alarmes.

Si tout le lot est refusé avant création, corriger et soumettre de nouveau.
Après résultat partiel, garder les identifiants réussis. Réponse perdue :
comparer buts, heures et identifiants, pas répéter aveuglément. Retirer
l’intention dépassée par `unschedule(task_id)` et vérifier. Si l’existence
reste inconnue, dire et examiner l’incertitude.

## Types et temps

Dans `remind_self(kind=...)`, suivre le schéma actuel :

- **wake** : tour vivant pour vérifier et poursuivre.
- **window** : travail concentré ; canaux selon l’exécuteur, pas promesse
  de déconnecter tout Telegram.
- **message** : intention ; un tour vérifie contexte et destinataire
  avant l’envoi. L’horloge n’envoie pas le texte seule.
- **note** : rappel pour moi ou le propriétaire.
- **email** : intention de courriel ; envoi selon `email.md` et le transport actuel.

Expressions canoniques : `in 2h`, `in 30m`, `today 14:00`, `tomorrow 10:00`,
date ISO, `daily 09:00`. Ne pas traduire ces valeurs de protocole.
Sans heure, la tâche peut devenir éligible au prochain passage, sans
garantie d’exécution immédiate malgré pause ou occupation.

## Initiative et limites

Transformer une demande explicite en plan ou action disponible avec
`remind_self(kind=...)`, `send_message` ou `send_email`, selon le droit
de livraison. Confirmer seulement ce qui a été créé ou effectué. Mes
questions ouvertes peuvent aussi être planifiées, sans contourner
Stop/Pause ni accord de dépense.

`my_agenda` montre les intentions ; `unschedule(task_id)` retire la choisie.
Les fils d’attention passent par `manage_loop` ; voir `thread_discipline.md`.
Une promesse précise reçoit son mécanisme de retour dans le même tour.

Planificateur, tâches durables, destination et reçus aident à reprendre,
sans prouver le résultat. Ne promettre ni absence universelle de limites
ni protection de tout transport contre les doublons. Vérifier la messagerie
dans `my_capabilities`.
