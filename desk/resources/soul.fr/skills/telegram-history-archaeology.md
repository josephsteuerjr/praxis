# Archéologie de l'historique Telegram

`evidence_source` est une ancienne source étayant une conclusion. `live_target` est la destination actuelle, établie séparément, d'un nouveau message. La première ne devient jamais automatiquement la seconde. Ancien message, suivi, résultat de recherche ou fil ouvert ne sont pas une nouvelle invitation à répondre.

Avant l'envoi, nommer le motif : demande entrante actuelle, demande explicite de revenir au sujet ou initiative délibérée autorisée. Établir séparément pair exact, sujet/fil et `reply_to`. Lire la fin récente et la chaîne locale : le contexte a pu changer pendant la recherche. Sans destinataire, garder le résultat en interne ; pour un retour voulu, annoncer la transition plutôt que répondre techniquement à l'ancienne source par défaut.

Commencer par le salon exact et un échantillon ciblé :

- Pair et `message_id` connus : lire message, parents et fenêtre voisine.
- Phrase connue : `messages.SearchRequest` disponible selon le schéma exact.
- Auteur précis : filtre `from_id` en gardant le contexte des réponses d'autrui.
- Conversation : réunir fil et période, pas seulement le corpus d'un auteur.
- Schéma inconnu : consulter d'abord recherche du registre et description de `telegram_account`.

Paginer par lots limités. Garder `chat_id`, sujet/`top_msg_id`, racine de chaîne, identifiant du message, date UTC, expéditeur et fenêtre. Ne pas fusionner des sujets voisins. L'expéditeur n'est pas forcément l'auteur des citations, transferts ou collages. La propriété d'un texte/projet exige source directe ou technique ; sinon, c'est une hypothèse.

Distinguer `found`, `zero_within_bounded_coverage`, `partial`, `tool_error` et `peer_unresolved`. Seul un zéro borné permet une conclusion négative dans les limites nommées. Pour « premier », « dernier » ou « jamais », suivre période, pages, bord atteint, erreurs et trous ; une réponse locale ordinaire n'exige pas un scan complet.

Donner une adresse source minimale vérifiable ; distinguer citation, résumé et déduction. Transmettre seulement à un public autorisé ; même le caractère public ne justifie pas une divulgation massive sans raison.

S'arrêter à la précision requise ou quand étendre le périmètre ne changerait plus la formulation.
