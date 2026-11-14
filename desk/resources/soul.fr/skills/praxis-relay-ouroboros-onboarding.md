# Utiliser un abonnement ChatGPT via Relay

Il s'agit d'un parcours de connexion, pas d'un état éternel des versions. Établir produit, OS et version : relay intégré à Hélène et praxis-relay autonome pour Ouroboros ont commandes et distribution distinctes.

Dans Hélène, utiliser les réglages d'abonnements et la connexion proposée. Ne pas installer un second relay ni arrêter celui intégré pour suivre un ancien menu. Prendre options, builds et méthodes de connexion dans l'application actuelle.

Pour un client distinct :

1. Vérifier version officielle actuelle et documentation. Télécharger le build adapté à l'OS/architecture ; ne pas promettre un artefact absent.
2. Terminer la connexion OAuth/device disponible à son propre compte. Ne jamais transmettre les identifiants ; vérifier le besoin de Python externe selon la version.
3. Lancer le relay et le laisser actif pour le client.
4. Dans Ouroboros, choisir le fournisseur OpenAI-compatible pris en charge et l'URL locale réelle. `http://127.0.0.1:5011/v1` est un exemple de défaut autonome, pas l'adresse d'une autre machine ni un réglage garanti d'Hélène.
5. Vérifier exigences de clé et préfixes de modèles dans le client/relay. Ne pas employer un autre jeton actif comme chaîne décorative. Consulter les modèles via `/v1/models`, pas une ancienne liste.

Diagnostic au besoin : `/health` contrôle le serveur local ; `/v1/limits` donne les informations de quota disponibles. Un état sain ne prouve pas l'acceptation du fournisseur. À épuisement, la bascule prise en charge entre comptes déjà autorisés peut aider ; ne pas rejouer une requête partiellement exécutée ni promettre une capacité infinie. Si tous les quotas adaptés sont épuisés, afficher cause et heure de rétablissement si connues.

Avant l'arrêt, distinguer son processus autonome du relay de l'application et des autres services. Révoquer par la procédure prévue ; ne pas effacer un dossier auth arbitraire d'après un ancien chemin.

C'est un pont non officiel vers son compte ChatGPT, pas un compte OpenAI API ni une conversion d'abonnement Claude en Anthropic API. Offres et amont peuvent changer ; consulter les sources primaires actuelles pour aider quelqu'un.
