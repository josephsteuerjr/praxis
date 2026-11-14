# Parole et livraison

Un texte rédigé et un message livré sont deux événements distincts. Prendre le nom de l'opération d'envoi dans les outils actuels : la présence de `reply` dans un ancien tour ne prouve pas sa disponibilité maintenant.

- Un reçu réussi confirme l'acceptation par le transport d'un message précis pour un destinataire précis, pas sa lecture humaine.
- `stay_silent` enregistre un choix explicite de ne pas répondre lorsque cette opération existe.
- L'absence d'appel d'envoi ne prouve ni échec ni silence délibéré : il peut s'agir d'une note interne, d'un travail sans destinataire ou d'un tour inachevé. Examiner le canal et la trace d'exécution.
- Terminer un passage autonome sans destinataire n'oblige pas à chercher quelqu'un à qui envoyer le résultat.

Un `SENT` textuel ne prouve pas la livraison d'un média ; un RPC accepté ne confirme pas toujours la pièce jointe voulue. Ne jamais répéter aveuglément un envoi au résultat inconnu.

Le travail peut continuer après l'envoi. Une livraison réussie ne doit pas disparaître de l'historique si l'appel modèle suivant échoue. Une ancienne expérience avec `PRAXIS_CHAT_REPLY_HAND` ne prouve pas que ce drapeau est actif ici : distinguer lecture du code, test du transport et configuration actuelle.
