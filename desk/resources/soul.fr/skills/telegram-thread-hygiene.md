# Fermer un sujet et son suivi

À la clôture, vérifier suivis et alarmes dans le même tour. Utiliser
les opérations disponibles `telegram_account` cancel_followup et
`unschedule` pour retirer les suites sans fondement. Les paramètres
exacts viennent du schéma.

Avant de résumer un processus de fond, vérifier maintenant état réel,
heure et résultat. Sous Windows, employer l’inspection du harnais ;
`os.kill(pid, 0)` n’est pas un contrôle sûr d’un PID Windows. Une ancienne
trace de contexte ne prouve pas que le processus vit encore.

Avant un envoi d’initiative, rétablir destinataire et sujet exacts.
Pour les homonymes, comparer chat_id à la source ; un ancien fil ouvert
ne choisit pas la destination d’un nouveau message.
