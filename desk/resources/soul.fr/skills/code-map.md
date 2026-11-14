# Carte de mon code

Une carte aide à trouver un module ; les fichiers et résultats actuels
établissent les faits. `КАК-УСТРОЕН-HELENE.md` est utile, mais peut vieillir.
L’organisation dépend de la plateforme, des transferts et de l’édition.

| Emplacement | Quoi chercher | Comment vérifier |
|---|---|---|
| `data/` | mémoire, âme, fichiers de travail, éventuel dépôt Git des données | vérifier chemin, inventaire et historique réels |
| `tree/` | noyau : `agent.py`, `frame_*.py`, `memory_*.py`, `llm.py`, `forge.py`, `self_model.py` | lire le code ; établir la racine de tâche avant d’écrire |
| `app/localharness/` | démarrage, naissance, canaux, modes, transfert et mises à jour | vérifier implémentation et droits du répertoire |
| `app/python_app.py`, `app/first_start_window.py` | fenêtre Python et premier lancement | distinguer sources et interface compilée |
| `app/deskd/` | canal HTTP du programme et modèles de vue | vérifier route, autorisation et réponses réelles |
| `app/static/`, `app/mobile/`, `app/miniapp/` | interfaces compilées | trouver sources et commande de compilation ; actualiser les surfaces concernées |
| `runtime/` | interpréteur et dépendances | distinguer ces éléments des sources de l’application |
| `helene.json` | configuration, dont secrets | utiliser les réglages autorisés ; ne pas publier son contenu dans un canal partagé |

Lanceur, service, relais et composants optionnels ont leurs propres sources
et processus. Toute l’enveloppe n’est pas du Rust, ni tout le produit du
Python. Windows MCP est un module installé séparément sous sa propre
licence ; son schéma dans le contexte ne prouve pas la connexion du serveur.

## Trouver la cause

1. Établir version, chemin et processus servant le scénario défaillant.
2. Ouvrir un journal actuel et vérifier sa date. Un vieux journal ne
   prouve pas l’état du processus présent.
3. Trouver le gestionnaire avec `fs_search`, `coding_inspect` ou `code_query`.
   Vérifier appelants, schéma et complétude de la recherche, pas seulement un nom.
4. Comparer sources et octets réellement compilés et exécutés. Modifier
   une source ne change pas automatiquement un processus existant.

Les journaux peuvent s’appeler `runner.log`, `helene.log`, `service.log`
ou `broker.log` ; j’en trouve l’emplacement dans cette installation.
`memory/runs/` contient les tours disponibles, sans promettre une archive
complète de chaque acte. Avant de partager, je vérifie secrets et destinataire.

Git des données, Git des sources, copie Forge et instantanés de mise à jour
sont des historiques distincts. Je vérifie lequel contient vraiment le
fichier voulu. Application et retour : [my-own-code](my-own-code.md) ;
accès et élévation : [fence-and-broker](fence-and-broker.md).

Le piège principal est de tenir un chemin familier pour acquis. Je le
revérifie après mise à jour, transfert ou changement de plateforme.
