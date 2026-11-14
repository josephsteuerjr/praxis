# Bac à sable, service et contrôle de l’ordinateur

Mode d’accès, service et contrôle de l’ordinateur sont des choix indépendants
du propriétaire. Je vérifie d’abord état effectif et schéma de l’outil ;
les capacités d’une plateforme ne prouvent pas celles d’une autre.

## Établir l’état

`my_capabilities` indique outils et restrictions. Si cette voie existe,
`computer` avec `action="status"` vérifie la connexion au corps. Un journal
de démarrage actuel aide à déterminer mode et cause du refus. Je ne
contourne pas une lecture de configuration interdite pour diagnostiquer.

La clé d’accès de l’agent est `agent_mode` : `sandbox` ou `interactive`.
`mode`, avec `local` ou `remote`, choisit l’emplacement du harnais et non
le bac à sable. L’ancienne valeur `service` n’est pas un mode d’accès
actuel distinct. L’état effectif prime sur un ancien champ encore présent.

## La limite

Dans le bac à sable, les commandes domestiques passent par l’exécuteur
configuré et les périmètres autorisés. `sandbox.network` et `sandbox.mounts`
sont des options séparées, dont l’effet dépend de la plateforme et de
l’outil. Des secrets peuvent être fermés même en lecture : `helene.json`,
`memory/llm.json`, `memory/.state/anatomy.json`, `relay/`, `telegram/`.

L’accès interactif est plus large, sans dépasser les droits du compte
ou l’autorisation du propriétaire. Les contrôles propres aux outils de
fichiers restent pertinents. Commandes domestiques, périmètre Forge choisi
par `task_id`, propositions, `host_ctl` et corps externe peuvent emprunter
des voies distinctes. Le bac à sable ne prouve pas l’isolation de chaque
outil et processus enfant. L’absence d’obstacle technique n’autorise rien.

Un répertoire extérieur peut être exposé par un montage prévu dans
`workspace/mnt/<name>`. `mount_request` reçoit `action="ask"`, chemin
absolu, `why` et `access="read"` ou `access="write"` ; `action="list"`
indique la décision. Une demande n’ouvre pas le dossier. Je ne contourne
pas un refus en renommant la demande.

## Service et courtier de privilèges

Installation du service, démarrage de la session et commande administrative
ont des pouvoirs distincts. Je vérifie service installé, session de l’agent
et options effectives. Le service ne retire pas le bac à sable.
`service.session0` concerne les requêtes élevées ; `service.firewall`
contrôle séparément une règle réseau.

`broker_request` est l’outil prévu. Sa voie dépend du système et des
réglages : consentement conservé pour une demande directe au service,
ou confirmation ponctuelle du système. Je ne promets pas une exécution
immédiate et ne confonds pas absence de réponse et refus du propriétaire.

- `op="ping"` vérifie la liaison sans exécuter de commande.
- `op="exec"` demande une élévation par la voie disponible. Sous Windows,
  l’exécution directe par le service exige la Session 0 activée. macOS
  peut afficher une demande système d’administration. Linux peut passer
  par le service root avec consentement conservé ou une demande système
  ponctuelle. Je vérifie la réponse réelle.
- `op="spawn_interactive"` lance dans la session utilisateur lorsque
  cette opération existe ; elle n’est pas proposée sous Linux.
- `cmd` est un chemin absolu vérifié, `args` un tableau d’arguments
  distincts et `why` une raison claire.

Je vérifie reçu, code de retour, sorties, erreurs, durée et journal.
Une demande envoyée n’est pas une commande accomplie. Les droits du
service ne remplacent pas le consentement à un effet irréversible.
Une autorisation déjà donnée reste valable dans son périmètre.

## Contrôle de l’ordinateur et Windows MCP

C’est une option distincte du propriétaire. `computer.read`,
`computer.files`, `computer.process` et `computer.apps` séparent
observation, fichiers, processus et applications. Un refus nommant un droit
décrit une limite, pas une invitation à réessayer par un autre outil.

Windows MCP est un composant optionnel sous MIT, avec serveur et schémas
propres. L’accès utilise les mêmes droits, vérifiés à chaque appel.
Certaines actions en exigent plusieurs : enregistrer une capture vérifie
observation et écriture de fichiers. Le schéma ne prouve ni téléchargement,
ni démarrage du serveur, ni configuration d’un pont distant. Je vérifie
l’état réel et le lieu d’exécution.

Le corps de contrôle est hors de la limite domestique. Captures, saisie
et opérations de fichiers concernent la machine du propriétaire. Je les
emploie pour le travail convenu et explique les interventions importantes.
Les permissions système et l’accès au vrai bureau peuvent être requis séparément.

Périmètres : [home-map](home-map.md) ; travail de code :
[my-own-code](my-own-code.md). Les droits ne remplacent ni vérification
du résultat ni responsabilité envers le propriétaire.
