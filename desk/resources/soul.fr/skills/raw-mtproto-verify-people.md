# Vérifier des faits sur une personne avec MTProto

Si les outils ordinaires ne fournissent pas un fait nécessaire, consulter le schéma disponible de `telegram_account` : `registry_search` → `describe` → `call`. Utiliser le nom exact du constructeur avec `functions.`, par exemple `functions.users.GetFullUserRequest`. Prendre paramètres et résolution des entités dans le schéma actuel ; ne pas supposer que `access_hash` existe pour tout identifiant.

Un profil peut contenir nom, présentation, statut, salons communs et autres champs selon l'API, la confidentialité et la disponibilité. Ne pas promettre l'accès à la musique, aux heures exactes de connexion ou au téléphone. Voir un champ n'autorise pas à le transmettre à un autre public.

Identifier la personne par un identifiant et une source vérifiés. Si l'identifiant est inconnu, noter que la vérification n'a pas été faite. Un nom identique ou un ancien résumé ne suffit pas. Ne mettre à jour que les faits utiles à la tâche, avec date et source ; respecter les règles de stockage et de sauvegarde de l'installation.

Avant de fusionner des dossiers, retrouver les preuves initiales des affirmations contestées et distinguer auteur et personne citée. Conserver les anciennes références ou une redirection explicite et un retour possible ; ne pas supprimer les doublons avant contrôle d'intégrité. Fusionner deux fichiers ne valide pas leurs affirmations contradictoires.
