# Modération précise par MTProto

Le nom du fichier est historique : il ne donne aucun pouvoir de modération. Avant d'agir, vérifier la politique actuelle du propriétaire, le pair exact, le sujet et les droits du compte. Ne pas transposer les règles d'un groupe à un autre.

Avec `telegram_account`, commencer par `registry_search`, puis `describe` et enfin `call` avec le constructeur exact disponible. `callable` indique une disponibilité technique, pas le consentement du propriétaire à tout usage. Vérifier le schéma actuel et les entités requises.

- `functions.channels.GetAdminLogRequest` aide à identifier l'auteur d'une suppression ou d'un bannissement. Comparer événement, participant, date et objet ; la proximité temporelle ne prouve pas une chaîne causale.
- `functions.channels.EditBannedRequest` peut modifier les restrictions sans message encore visible. Vérifier canal, participant, droits et durée ; ne pas choisir un bannissement permanent par défaut.
- `functions.channels.DeleteParticipantHistoryRequest` supprime l'historique d'un participant : c'est une action destructive distincte, qui exige l'autorité correspondante.

Lire le message complet et son contexte : le déclencheur peut être tronqué. Un texte technique contenant des pourcentages ou des mots commerciaux n'est pas forcément du spam. Un `prior_measures` vide indique qu'aucune mesure n'a été trouvée, pas que la personne est nouvelle. `already_deleted` ne justifie ni une nouvelle suppression ni une sanction aggravée automatiquement.

Étayer le résultat par le reçu de l'appel précis. Distinguer message inaccessible, refus et résultat inconnu ; ne pas répéter aveuglément une mutation après expiration du délai. Ne pas deviner l'adresse d'un salon privé à partir de son nom.
