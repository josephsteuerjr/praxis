# Répondre dans un forum : vérifier la destination séparément

Un texte juste peut arriver dans le mauvais fil. Avant l'envoi, distinguer pair, sujet et message parent ; lire un sujet voisin ne change pas la destination du tour actuel.

Dans le code actuel, `_route_and_reply` analyse `ОТВЕТ->#<id>` à partir des `origin_message_ids` autorisés. Si la directive n'est pas acceptée, un tour de groupe adressé utilise `address_message_id` ; sinon, `route.topic_id`. Un identifiant inconnu n'arrête pas l'envoi : le repli s'applique. Lire un message ne prouve pas que son identifiant est autorisé.

Pour un autre fil, lire d'abord son contexte avec `group_context`. Choisir ensuite une opération disponible avec une destination explicite et vérifiable et `reply_to`, ou une directive confirmée valide. Un ancien format d'adresse n'est pas universel pour tous les transports.

Vérifier le reçu : pair, sujet et parent réels, s'ils figurent dedans. Sans sujet dans le reçu, cette partie de la livraison reste non vérifiée. Corriger les défauts de routage dans le code ; une habitude ne compense pas une liaison de transport incorrecte.
