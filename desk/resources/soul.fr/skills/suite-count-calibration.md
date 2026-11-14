# Comparer les identifiants de tests, pas un ancien total

Un total exige HEAD, lanceur, périmètre de découverte et conditions de saut. Les anciens nombres d'un autre passage ne fixent pas la norme de la version actuelle.

`praxis_test.py`, `unittest discover` et `pytest` peuvent collecter des périmètres différents. Examiner découverte actuelle : sous-dossiers, paquets, tests sous forme de fonctions et contrôles propres aux plateformes. Ne qualifier l'écart ni de trou ni de réussite sans liste.

1. Obtenir les listes réelles, par exemple via `--collect-only` et discovery.
2. Normaliser `file.py::Class::test` en `file.Class.test` ; comparer ensembles et doublons séparément.
3. Rapprocher sauts, échecs et erreurs par identifiant complet et raison.
4. Tester les hypothèses de collecte avec AST ou petit script. Si les comptes divergent, réviser l'hypothèse, pas les données.
5. Garder les totaux attendus avec commande, HEAD et plateforme ; ne pas les reporter de mémoire dans une autre version.

Une sortie verte avec une collecte réduite de façon inattendue exige un contrôle de couverture. Des tests supplémentaires empêchent aussi de déclarer les passages équivalents. Vérifier séparément l'héritage de l'environnement.
