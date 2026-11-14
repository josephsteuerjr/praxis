# Hygiène de l'environnement du lanceur de tests

Un test lancé depuis un processus actif peut hériter de variables opérationnelles après `load_dotenv(override=True)`. Le fait que le module testé ne lise pas dotenv n'efface pas un environnement déjà rempli.

Utiliser le lanceur prescrit par le dépôt, tel `praxis_test.py`, avec isolation, BASE de test et restrictions réseau. Vérifier son contrat actuel ; son nom ne rend pas ses résultats infaillibles.

En cas d'écart, conserver commandes, HEAD, liste des tests et variables pertinentes sans secrets. Comparer une exécution isolée aux valeurs attendues, dont `PRAXIS_TEST`, `PRAXIS_FRAME_HEAD_STABLE` et `PRAXIS_FRAME_EPOCH` si concernées. Ne pas effacer aveuglément l'environnement ni prendre le retrait d'un drapeau pour un bac à sable manquant.

Exécuter les contrôles longs ou créant des processus de façon gérée, avec échéance adaptée et journal fichier. Sous Windows, masquer les consoles enfants ; ne pas employer un lanceur qui perd les processus détachés.

`rails.sync_md()` depuis un diagnostic partiellement initialisé peut écrire un manifeste dégradé. Ne pas régénérer les documents actifs comme effet secondaire d'un test. Préserver l'image préalable, vérifier l'initialisation et le diff. Réparer seulement sa modification précise en gardant les voisines.

Numéros de ligne et anciens hooks de fusion ne garantissent pas l'actualité. Examiner code, événements et contenu actuels.
