# Faits de modération MTProto

Pour droits ou actions passées, vérifier compte actuel, adresse du canal
et registre `telegram_account`, sans hériter des pouvoirs d’une autre installation.

Pour le journal, examiner `functions.channels.GetAdminLogRequest` : canal,
bornes/limit et events_filter. Comparer event_id, acteur et heure.
Une notification de suppression ne prouve seule ni auteur ni autre action.

Le registre peut proposer `functions.channels.EditBannedRequest` et
`functions.channels.DeleteParticipantHistoryRequest`. Le constructeur
ne donne ni but, ni droits, ni règle du salon : les établir par demande
et contexte actuels. Paramètres et permissions viennent du schéma vivant.
Ne pas répéter une action close sur le retour d’un ancien déclencheur.

Un message tronqué reste partiel. Lire texte complet et contexte si
accessibles avant de conclure. Confirmer l’acte par son reçu prévu.
