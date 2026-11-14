# Réponses dans les commentaires d’un canal

Les commentaires Telegram sont un fil du groupe de discussion rattaché
à une publication précise.

- Répondre au message de la publication dans le groupe ou à un message
  de son fil pour apparaître sous cette publication.
- Ne pas créer de message racine séparé dans le flux général.
- Vérifier `reply_to` et le sélecteur de fil avant l’envoi.
- Après une erreur de destination, ne pas dupliquer aveuglément :
  trouver publication ou fil exact et y livrer la correction.

Un groupe de discussion peut utiliser un fil de réponses plutôt qu’un
sujet de forum. Se fier à `top_msg_id`, la carte des cibles et `reply_to`
réel, pas au seul mot « sujet » de l’interface ou de l’aide.
