# Livraison inconnue après reconnexion

Un `TimeoutError` de réponse peut clore l’attente synchrone tandis que
l’envoi durable continue. L’exception ne prouve ni reconnexion comme
cause ni livraison future.

Examiner reçu, boîte d’envoi et destination exacte via `read_chat`
disponible. Si confirmé ou visible, ne pas dupliquer. Sinon garder
état et identifiant, puis interroger par la voie prévue. Un suivi
pending n’est pas automatiquement clos : vérifier état et réponse actuelle.

Étudier une longue série d’échecs dans transport et code. Vérifier
le calcul de fond séparément : l’erreur peut finir le tour sans finir
le processus. Relancer ne contrôle pas le premier essai. Préserver
processus et tâches sans rapport.
