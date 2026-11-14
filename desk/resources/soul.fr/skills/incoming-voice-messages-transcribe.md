# Messages vocaux reçus

Un marqueur vocal sans texte ne signifie pas qu’une instruction a été
lue. Vérifier la transcription dans l’événement et, au besoin,
`events.jsonl`. Trouver l’original par le vrai chemin du message, par
exemple `memory/runs/<run>/artifacts/`, sans deviner son nom.

Si le composant audio existe, employer `media_audio.transcribe(path)`
ou l’outil actuel. Ne supposer ni Whisper installé, ni durée fixe de
25 secondes, ni limites identiques. Une opération longue conserve
identité, délai et suivi prévus, sans processus de fond abandonné.

Lire le résultat et effectuer la tâche comprise et autorisée. Garder
l’important avec source dans le bon contexte privé. En cas d’échec,
dire que la transcription manque et demander du texte ou une reprise
possible. Ni deviner le contenu ni remplacer la réponse par le silence.
