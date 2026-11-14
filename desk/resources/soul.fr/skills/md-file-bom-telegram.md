# Encodage d’un texte pour son destinataire

Certaines versions d’aperçu mobile ont mal identifié UTF-8 sans BOM,
produisant des caractères illisibles malgré une lecture correcte ailleurs.
C’est une classe de compatibilité connue, pas une affirmation sur toutes
les versions actuelles de Telegram ou de téléphone.

Pour un destinataire concerné, préparer une copie d’envoi distincte avec
BOM UTF-8, éventuellement via `utf-8-sig`. Vérifier octets `EF BB BF`,
contenu et aperçu cible. Ne pas doubler le BOM ni modifier source,
empreintes ou fichiers de programme pour un aperçu. Avant de renvoyer,
vérifier le reçu plutôt que dupliquer aveuglément le message.
