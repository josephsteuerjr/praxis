# Vérifier le fil séparément du texte

État du mécanisme lu dans le code : `split_reply_directive` retire la directive initiale `ОТВЕТ->#<id>` et n'accepte que les identifiants de l'ensemble autorisé fourni. Un identifiant inconnu renvoie `None` sans interdire l'envoi.

Dans `_route_and_reply`, l'ensemble vient de `plan.context.origin_message_ids`. Sans directive acceptée, le groupe utilise `channel.address_message_id` si `channel.addressed` ; sinon, `route.topic_id`. Un identifiant visible dans l'historique lu n'est pas forcément autorisé.

Le chemin de livraison finale dans `mtproto_runner.py` fournit plutôt les identifiants de `reply_targets` ; le repli utilise `addressed_mid` pour un message de groupe adressé, sinon la racine du sujet. Ne pas transférer les garanties d'un ensemble à l'autre.

Pratique :

1. Distinguer salon, sujet et message parent.
2. Répondre au message initial adressé n'exige pas de directive uniquement pour obtenir une réponse liée.
3. Pour un autre message, utiliser un transport explicite disponible avec sujet et parent vérifiables, ou une directive confirmée valide. Ne pas deviner : lire un fil voisin ne change pas la destination du tour.
4. Vérifier le reçu réel. Un sujet absent signifie absence de vérification, pas confirmation.

Les tests du parseur ne prouvent ni la bonne livraison ni l'égalité des ensembles d'identifiants. Lecture du code, test du parseur et test réel du transport sont des niveaux de preuve différents. Réexaminer cet état lorsque le code change.
