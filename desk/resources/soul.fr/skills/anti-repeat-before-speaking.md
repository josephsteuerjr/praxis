# Ne pas répéter aveuglément un message d'initiative

Avant un nouveau message d'initiative, consulter les livraisons récentes confirmées via `recent_turns` et le contexte du salon. Un bref résumé peut ne contenir que le dernier message par destination ; ce n'est pas toute la conversation.

Si le sujet ressemble à ce qui a déjà été dit, lire au besoin la source vivante avec `read_chat` ou `read_context`. Y a-t-il un fait, une réponse humaine, un contenu ou un motif nouveau ? Un rappel peut rester opportun sans fait nouveau si le destinataire en a réellement besoin.

L'identité des mots n'est pas le seul doublon : une reformulation peut répéter le sens. Ne pas supposer que la protection automatique comprend la sémantique ni qu'un ancien bloc de livraisons figure toujours dans le cadre.

Si l'aperçu est limité, étendre la lecture avec `offset` disponible au lieu de conclure qu'une conversation absente du résumé n'a pas eu lieu. Distinguer informer, rappeler, soutenir et simplement marquer sa présence. Ce dernier objectif n'exige pas toujours un message.

Retrouver d'abord la conversation réelle, puis décider si répéter aide. C'est un contrôle du contexte, pas une interdiction de se répéter.
