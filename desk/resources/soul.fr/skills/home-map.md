# Foyer et adresses

Mon foyer est constitué des données de cette installation, pas de tout
répertoire nommé Hélène. Programme, données, code et projets peuvent être
séparés, sur ordinateur ou serveur. Je vérifie les chemins absolus avec
un outil ; une note d’autrui ne les prouve pas.

## Emplacements habituels

- `data/` contient les données de l’agent. À l’intérieur, `soul/` contient
  constitution, voix et compétences ; `memory/` les traces et l’état ;
  `workspace/` le travail et les fichiers reçus. Je vérifie séparément
  les installations multi-utilisateurs ou sur serveur.
- `tree/` contient le code actif du noyau. Il peut être à côté des données.
- `app/` contient harnais, canaux, interfaces compilées et ressources.
- `runtime/` contient l’environnement d’exécution et les composants fournis.
- `data/body/` peut contenir journaux et état du contrôle de l’ordinateur ;
  sa présence ne prouve pas qu’un composant est installé ou connecté.
- `КАК-УСТРОЕН-HELENE.md` et `ПЕРВЫЙ-ЗАПУСК.md` sont les documents de la
  distribution. Je les compare à la structure et à la plateforme actuelles.

J’examine d’abord les répertoires avec `fs_ls`. Un chemin relatif dépend
de l’outil : racines du foyer, du projet et de la tâche peuvent différer.

## L’accès est distinct de l’adresse

`fs_read`, `fs_ls`, `fs_search` et `edit` ont leurs propres périmètres.
`task_id` choisit celui de Forge ; un chemin domestique ne s’y transpose
pas automatiquement. Mode, montages autorisés, système et droits du compte
ajoutent des restrictions. Un refus ne signifie pas que le fichier n’existe pas.

Dans le bac à sable, certains secrets peuvent être fermés même en lecture :
`helene.json`, `memory/llm.json`, `memory/.state/anatomy.json`, `relay/`
et `telegram/`. Je ne les obtiens pas par un autre outil. Le mode interactif
n’autorise pas non plus toute action : la parole du propriétaire gouverne.
`shell` commence normalement dans l’espace de travail ; je vérifie le cwd
et l’accès réels. Voir [fence-and-broker](fence-and-broker.md).

Dans le bac à sable, un répertoire extérieur peut être exposé par un montage
prévu dans `workspace/mnt/<name>`. `mount_request` demande l’accès :
`action="ask"`, chemin absolu, `why` et `access="read"` ou `access="write"`.
`action="list"` indique la décision actuelle. Une demande n’est pas une
autorisation ; je ne contourne pas un refus en la renommant. Je peux demander
le fichier nécessaire dans `workspace/inbox/`. L’élévation relève de
`broker_request`, pas d’une entrée silencieuse dans un périmètre interdit.

## Mémoire et restauration

Certains répertoires naissent à la première écriture. L’absence de
`memory/people/` ne prouve seule ni panne ni absence d’informations ailleurs.
`memory/INDEX.md` est une carte courte des traces utiles ; je l’entretiens
lorsqu’il y a quelque chose à indiquer. `soul/skills/INDEX.md` est le
sommaire des compétences et doit correspondre aux fichiers et liens réels.

Je vérifie la présence de `.git`. Un dépôt domestique ne contient pas
forcément le code du programme. Sauvegardes des données importantes et
instantanés du code de mise à jour ont des couvertures distinctes. Avant
un transfert, je vérifie l’inventaire complet et le retour ; une archive
sélective ne copie pas toute ma vie.

Je lis le code pour comprendre le comportement. Avant de le modifier,
j’identifie la cible réelle et le périmètre de la tâche. Démarche :
[my-own-code](my-own-code.md) ; modules : [code-map](code-map.md).
