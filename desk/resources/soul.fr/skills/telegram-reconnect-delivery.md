# Délai Telegram et livraison réelle

Lors d’une reconnexion Telethon, l’envoi peut dépasser l’attente de
l’enveloppe synchrone et aboutir plus tard par une boîte d’envoi durable.
Distinguer erreur d’attente et résultat d’envoi.

Examiner d’abord reçu, boîte d’envoi et historique à l’adresse exacte.
Si un vrai message_id Telegram existe ou si le message est visible,
ne pas renvoyer. Si l’issue reste inconnue, conserver et examiner cette
incertitude. TimeoutError ne prouve ni livraison ni absence.

Vérifier le calcul de fond séparément : tour interrompu et processus
vivant sont distincts. Employer l’idempotence prévue pour la reprise.
Une longue série d’erreurs demande d’étudier transport et code ; une
reconnexion n’explique pas d’avance toute panne.
