# Examiner un ensemble stochastique et OAT

Pour Monte Carlo/ensembles, vérifier la causalité de la comparaison autant que formules et percentiles.

1. Avant calcul, noter l'énoncé littéral et les hypothèses devenues paramètres. Ne pas remplacer sans le dire « ouragan » par écoulement stationnaire ou « chute » par une seule orientation. Séparer les lectures possibles.
2. Garder code original, hash et sortie complète ; exécuter avec isolation et pouvoirs adaptés. Un stdout reproductible ne prouve pas la validité physique.
3. Séparer variabilité interne au modèle, entre familles et incertitude des priors d'entrée. Plusieurs réalisations d'une famille ne sont pas des preuves indépendantes sur le monde.
4. OAT exige un monde aléatoire apparié. Une graine seule ne suffit pas si désactiver un facteur change l'ordre RNG. Échantillonner les primitives sans condition, puis modifier leur effet physique.
5. Estimer l'incertitude Monte Carlo par graines répétées, bootstrap ou SE. Un petit déplacement de médiane n'est pas un effet établi sans estimation du bruit.
6. Les écarts OAT dépendent de la base, cachent les interactions et ne s'additionnent pas nécessairement. Pour les contributions globales, choisir plan factoriel/Sobol adapté ou nommer la limite.
7. Vérifier la censure : `TMAX` laisse-t-il des trajectoires actives non comptées ? Examiner coordonnées, rayon, p95/p99 et probabilités des seuils utiles.
8. Vérifier les sources de `Cd(Re)`, crise de traînée, loi de rotation, sillage, masse et taille. Le nom d'un objet ne fournit pas ses paramètres ; tester la sensibilité aux priors contestés.
9. Séparer reproductibilité du code, résultat conditionnel du modèle et assertions non vérifiées sur l'objet réel.
10. Pour les effets vectoriels, examiner `with−without` apparié, axe, signe, culbute et queues. Une médiane nulle peut cacher des déplacements opposés ; un prior symétrique ne prouve pas la symétrie du corps.
11. Compléter « physiquement possible » par le coût du mécanisme : force, durée ou action intégrée requise. Un probe reproductible séparé avec dépendance au modèle vaut mieux qu'une possibilité nue ; `L/W≈0.58` illustre une condition d'un modèle précis, pas une constante universelle.
