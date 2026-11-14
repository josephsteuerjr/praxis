# Arrêt d’urgence de Hélène

Pour un travail dangereux, `panic(reason)` disponible conserve un arrêt
de l’installation et engage la fin du travail agent. Fermer une fenêtre
n’est pas équivalent : une session de fond peut rester vivante.

Le propriétaire utilise Arrêt d’urgence dans fenêtre, zone de notification
ou téléphone, le raccourci installé, ou `/panic` à son bot. La forme
`/panic@BOT_USERNAME` exige le vrai nom du bot. Ce chemin Telegram
vérifie le propriétaire configuré avant le tour du modèle.

Sous Windows, établir d’abord le vrai chemin.
CMD : `"<absolute-program-path>\helene.exe" --panic`.
PowerShell : `& "<absolute-program-path>\helene.exe" --panic`.
Si l’utilitaire est fourni, `helene-svc.exe panic` fonctionne depuis
son dossier. Ne pas supposer une installation dans Program Files.

Ouvrir, redémarrer ou mettre à jour ne consent pas à lever l’arrêt.
La reprise exige l’acte explicite du propriétaire ou la commande prévue
`helene-svc.exe resume`. L’agent ne contourne pas la décision par un
processus différé.

Le drapeau interdit un nouveau démarrage. L’issue des processus se vérifie
séparément et peut rester inconnue. Le drapeau ne prouve ni fin de l’arbre
ni annulation de toutes les actions extérieures déjà envoyées.
