# Horaire et texte d'un message différé

Avant de programmer, vérifier date actuelle, fuseau du destinataire et échéance interprétée. `parse_when("today 09:45")` peut produire une heure passée ; « aujourd'hui » ne signifie pas « le prochain matin convenable ». Près d'un changement de jour ambigu, utiliser date explicite et format de fuseau pris en charge.

Une tâche en retard peut partir au prochain cycle. Un avertissement d'échéance passée invite à corriger l'erreur, pas à l'ignorer.

Avec `kind=message`, ne mettre dans `goal` que le texte destiné à la personne. Notes internes « écrire au participant… », priorités et instructions peuvent être envoyées telles quelles. Si un raisonnement est nécessaire avant livraison, choisir le type de tâche disponible adapté.

Après création, examiner `my_agenda` : échéance future voulue, destination correcte et texte propre. Pour plusieurs destinataires, vérifier chaque livraison et résultat de création.

Si un brouillon technique est déjà parti, reconnaître l'erreur précise et en corriger le sens au moment opportun. Ne pas réveiller quelqu'un la nuit pour réparer un message matinal non urgent ; une demande explicite et urgente de réveil est différente. Ne pas répéter un envoi au résultat inconnu avant vérification du reçu.
