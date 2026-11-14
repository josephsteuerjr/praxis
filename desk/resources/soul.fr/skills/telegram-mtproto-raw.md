# Faits exacts par MTProto

Si les outils Telegram ordinaires ne suffisent pas, consulter
`my_capabilities` et le registre `telegram_account`. Il faut un compte
utilisateur Telethon connecté ; Bot API seul ne fournit pas MTProto.

Suivre `registry_search` → `describe` → `call`. Donner le constructeur
exact préfixé par `functions.`, par exemple `functions.users.GetFullUserRequest`.
`users.GetFullUserRequest` ne le remplace pas. Lire les paramètres du
schéma reçu et établir indépendamment identifiant ou username. Le transport
convertit l’entité en InputUser/InputPeer avec access_hash.

Le profil peut contenir nom, description, statut, salons communs et autres
champs. Garder date et source, sans déduire une personne d’un seul champ.
Sans adresse exacte, la vérification reste incomplète.

Lire et divulguer sont distincts. Téléphone, transferts privés et autres
conversations restent dans leur périmètre. Avant de fusionner des dossiers,
retrouver le fait contesté et distinguer expéditeur, voix citée et auteur du propos.
