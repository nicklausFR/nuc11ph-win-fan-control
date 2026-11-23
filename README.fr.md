# NUC11phi7 Cooling — interface Qt et moteur C

[English documentation](README.md)

Le matériel et les garde-fous sont gérés par un **exécutable C17 Windows x64 autonome**. Le fichier `python/nuc_client.py` transporte les commandes sans accès direct au matériel. **NUC Cooling**, dans `ui/`, fournit un panneau Python/Qt dans le systray : températures CPU/GPU/SSD, charges CPU et GPU NVIDIA, vitesses des ventilateurs, sélection des profils et ventilation forcée.

## Interface dans le systray

```powershell
python -m pip install -r ui/requirements.txt
python ui/app.py --show
```

L’interface se connecte au moteur existant ou le lance avec `--allow-write`. Aucun
mode n’est appliqué avant un clic sur **Appliquer** ; `--read-only` désactive les commandes. Un clic
sur l’icône affiche le panneau ; l’option Épingler du menu le garde au premier plan.
Les options proposent les thèmes clair/sombre/automatique, une actualisation de
1, 2 ou 5 s et le choix **Français / English / Langue de Windows**. La traduction
est immédiate et mémorisée ; elle conserve les réglages de ventilation en cours.
Par défaut, Windows français donne le français et les autres langues donnent l’anglais.
Le nom du profil correspond aux paramètres exposés par le catalogue du moteur.
Les mesures indisponibles affichent « — » et la reconnexion est automatique.
Les profils Silencieux/Équilibré/Refroidissement et la consigne commune forcée de
50 à 99 % utilisent les sessions et garde-fous du moteur. Le renouvellement est
indépendant de l’affichage ; la fermeture restaure la configuration initiale.

Pour un exécutable sans Python : installer `ui/requirements-build.txt`, puis lancer
`python tools/build_ui.py`. Ouvrir `dist/nuc-cooling.exe` avec `dist/nuc-engine.exe`
à côté. [Documentation de l’interface](ui/README.fr.md).

Le moteur utilise uniquement le pilote **Intel PerformanceDriver déjà installé** et les API Windows. Il n’installe aucun pilote, ne charge aucune DLL OEM, n’utilise pas WinRing0 et ne réactive pas NUC Software Studio. Python n’est pas nécessaire pour faire fonctionner le moteur C.

## Utilisation sans installation

Depuis le dossier de l’archive binaire :

```powershell
# Démonstration lisible : lance le moteur en lecture seule si nécessaire
python examples\demo.py
python examples\demo.py --watch

# Lecture ponctuelle directe, sans serveur
.\nuc-engine.exe status

# API locale en lecture seule
.\nuc-engine.exe serve

# API locale autorisant des commandes temporaires
.\nuc-engine.exe serve --allow-write
```

Depuis les sources, le code C et sa compilation sont dans `fan-control/`, avec l’exécutable dans `fan-control\build\nuc-engine.exe`. Le client API reste dans `python/` et l’interface Qt dans `ui/`. Le script `build.ps1` à la racine lance la compilation du moteur. Arrêt normal en console : `Ctrl+C`. La fermeture demande la restauration de la configuration sauvegardée.

Dans une autre console, avec Python 64 bits ≥ 3.11 :

```powershell
python python\nuc_client.py
python python\nuc_client.py --watch
```

Le client de diagnostic ne fait que lire. Aucun serveur HTTP, port réseau ou jeton sur disque n’est utilisé. Le canal nommé `\\.\pipe\NucControl.v1.<SID>` est limité au compte Windows propriétaire et à SYSTEM ; les connexions distantes sont refusées. Un seul moteur peut posséder ce canal.

## Client Python et interface

Ajouter le dossier `python` au chemin des modules de l’application, puis :

```python
from nuc_client import NucClient, NucError

with NucClient() as nuc:
    mesures = nuc.telemetry()
    print(mesures["temperatures"], mesures["fans"], mesures["ssds"])

    # Le moteur doit être démarré avec --allow-write.
    session = nuc.set_duty(60, lease_seconds=5)
    # L’interface peut renouveler avant expiration, sans recalcul matériel :
    nuc.renew(lease_seconds=5)
    nuc.restore()
```

Autres commandes : `set_profile("quiet" | "balanced" | "cool")`, `set_thresholds(primary_min_encoded, secondary_min_encoded, lease_seconds=5)` et `capabilities()`. Le client sérialise ses appels pour permettre son emploi depuis les threads de l’interface. Les appels pouvant attendre le pilote, les exécuter dans un thread de travail de la future interface.

Depuis la version 0.3, l’interface peut découvrir les profils et les garde-fous sans autorisation d’écriture :

```python
with NucClient() as nuc:
    print(nuc.profiles())        # Liste des profils avec libellés et paramètres OEM
    print(nuc.safeguards())      # Limites, règles, actions et champ d’application
```

`profiles()` renvoie `quiet` (Silencieux), `balanced` (Équilibré) et `cool` (Refroidissement), avec leurs pourcentages minimums et leurs seuils encodés. Cette lecture ne sélectionne aucun profil. Les minimums des profils firmware sont distincts de la borne de 50 % des consignes manuelles.

`safeguards()` expose la politique compilée dans le moteur, pas un diagnostic de santé instantané. `limits` fournit les bornes numériques ; chaque entrée de `rules` a un identifiant stable, un libellé, `applies_to`, une action et éventuellement une référence vers une limite. Les règles thermiques CPU/GPU s’appliquent à `duty` et `thresholds` ; les profils OEM conservent leur propre régulation. Ces garde-fous ne sont pas modifiables via l’API. Pour afficher le catalogue complet après démarrage du moteur : `python examples\catalog.py`.

La durée d’une commande est comprise entre **1 et 15 secondes**. Une interface qui maintient une commande doit envoyer un renouvellement avant expiration. La session est liée à son processus Python et à un identifiant aléatoire ; un autre client ne peut pas remplacer sa consigne. Tout client autorisé peut demander la restauration, qui est une opération de sécurité.

Si l’interface Python quitte ou plante, le processus C de récupération le détecte sans attendre l’expiration de la session. Le moteur C n’a pas besoin que Python continue à prendre les températures ni à effectuer les contrôles de sécurité.

## Fonctions et limites

| Fonction | Prise en charge |
| --- | --- |
| Températures CPU, GPU NVIDIA, GPU intégré | Pilote Intel |
| Tr/min CPU et GPU NVIDIA | Lecture séparée |
| Températures des deux SSD Crucial NVMe | SMART Windows ; cache de 10 secondes |
| Consigne directe | Canal partagé vérifié, 50 à 99 %, aucun arrêt des ventilateurs |
| Profils automatiques | quiet / balanced / cool, configurations OEM capturées |
| Seuils CPU/GPU | Valeurs encodées, temporaires, limitées à ±5 de la sauvegarde initiale |
| Régulation automatique | Profils et seuils exécutés par le firmware |
| Courbe logicielle personnalisée | Pas encore implémentée |
| Contrôle indépendant CPU/GPU | Non démontré, non exposé |
| Température spécifique carte mère | Non identifiée |
| RGB | Non implémenté |

Les seuils encodés observés sont `50 / 42`. Leur conversion en °C n’est **pas démontrée** ; l’API ne les présente donc pas comme des températures Celsius. Les sondes SSD additionnelles restent numérotées sans nom de composant supposé. Les températures SSD n’entrent pas dans le garde-fou CPU/GPU.

Les écritures sont limitées au NUC11PHi7 / NUC11PHBi7, BIOS `PHTGL579.0079.2024.1014.1949`, et au pilote dont le SHA-256 est `49526c5e9089ac9a7e2e0ef76329a97ac23f34d02e8913796814b8377acc1eed`. Les constantes sont dans `fan-control/src/hardware.c`. Une modification du BIOS ou du pilote bloque les écritures jusqu’à nouvelle validation. Le hash et la signature vérifiée du pilote installé ne constituent pas un audit de sécurité de ce pilote Intel.

Ne pas lancer en même temps l’ancien serveur du prototype Python : cette version C le remplace. Le verrou d’écriture reste compatible avec celui du prototype, pour refuser une prise de contrôle simultanée.

## Architecture et récupération

1. Le moteur C reçoit une requête binaire de taille fixe et valide ses champs.
2. Avant la première écriture, il verrouille le contrôle et sauvegarde la configuration automatique, soit 94 octets EC et son profil d’écriture de 90 octets.
3. Il lance un second processus du même exécutable C. Seuls les handles explicitement nécessaires lui sont transmis : mémoire partagée anonyme, événements, handles des processus et verrou d’écriture.
4. Ce processus de récupération devient l’unique émetteur des commandes pendant la session. Le processus principal continue à servir l’API et à lire les capteurs.
5. La restauration est relue et les 94 octets sont comparés à la sauvegarde. En cas d’échec, la récupération poursuit ses tentatives et conserve le verrou.

La récupération surveille la disparition de l’interface et du moteur, l’expiration, les événements de veille/reprise et, pour les consignes directes et seuils personnalisés, les températures et les tr/min. Une température CPU ≥85 °C, GPU NVIDIA ≥80 °C, une sonde indisponible ou un tr/min invalide déclenchent la restauration. Ces limites sont conservatrices pour ce prototype et ne représentent pas les limites matérielles des puces. Les profils automatiques OEM conservent leur régulation firmware.

La surveillance a une attente de 250 ms entre vérifications, à laquelle s’ajoute le temps des appels au pilote. Toute la transaction **ouverture / IOCTL / fermeture** du périphérique Intel partage le verrou OEM, pour empêcher un conflit d’ouverture entre les deux processus.

Le protocole de commande n’expose ni IOCTL libre, ni adresse matérielle, ni chargement de configuration arbitraire. L’API principale borne à 3 secondes les échanges incomplets sur son canal ; une interface bloquée n’empêche pas le processus de récupération de vérifier sa session. La fin d’une réponse est confirmée par un octet d’acquittement, ce qui évite de perdre les données lors de la déconnexion du canal.

Ce sont deux processus utilisateur : si les deux sont tués, si Windows plante ou si un appel au pilote reste bloqué, la restauration n’est pas garantie. Un langage compilé ne supprime pas ces limites. La version n’a pas encore subi un essai de longue durée ni un cycle réel de veille/reprise.

## Mode service Windows

L’exécutable comprend un hôte SCM (`service`) avec gestion de l’arrêt et des événements d’alimentation. Il n’est **pas installé automatiquement** : les validations actuelles portent sur le mode console et le processus de récupération.

Les scripts d’installation sont fournis pour une installation explicite, après validation. L’installation sous SYSTEM copie impérativement le binaire dans un dossier protégé de `Program Files`, dont les utilisateurs ne peuvent pas modifier le contenu ; ne pas enregistrer un service SYSTEM pointant vers le dossier Documents.

Dans la console normale du compte qui utilisera l’interface, relever son SID :

```powershell
[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
```

Dans PowerShell administrateur, depuis le dossier de l’archive :

```powershell
.\Install-Service.ps1 -OwnerSid 'S-1-5-21-…' -AllowWrite
Start-Service NucControlEngine

# Arrêt puis suppression du service ; le binaire est conservé.
.\Uninstall-Service.ps1
```

Le démarrage créé est **manuel**, sans activation au démarrage de Windows. Les scripts nécessitent des droits administrateur et n’ont pas été exécutés pendant la construction de ce prototype. Il reste à valider le fonctionnement sous le compte de service et la veille réelle avant de l’utiliser de façon permanente.

## Construction et validation

Windows x64, MSVC Build Tools 2022 et SDK Windows, avec `cl.exe` dans le PATH :

```powershell
.\build.ps1
.\build.ps1 -Analyze
.\build.ps1 -TestBackend
python -m unittest discover -s tests -v

# Vérification matérielle sans écriture
python tools\verify_hardware.py

# Essais courts explicitement activés, avec restauration et tests de panne
python tools\verify_hardware.py --write --crash

# Mesures en lecture seule : 120 lectures à quatre lectures par seconde
python tools\measure_engine.py
```

La compilation utilise C17, `/W4 /WX`, le runtime C statique et les protections `/GS`, `/sdl`, `/guard:cf`, ASLR et DEP. La version simulée est un exécutable distinct (`nuc-engine-test.exe`) qui ne parle pas au matériel réel. Ses commandes d’injection de panne ne sont pas compilées dans le binaire distribué.

Les tests matériels du 22 novembre 2026 ont confirmé les capteurs, Cool avec expiration, les seuils +1, 60 % avec relecture, la récupération après arrêt forcé de Python et après arrêt forcé du moteur C. Les réglages finaux correspondent exactement à la sauvegarde. Les pannes de sonde, de ventilateur, du processus de récupération et une notification d’alimentation sont aussi vérifiées avec le matériel simulé.

Les rapports locaux sont dans `test-results`, exclus des archives. Les deux archives contiennent l’exécutable de production, les sources C/Python, le script de construction et la documentation ; l’archive source inclut aussi les outils de vérification et les tests. Aucun pilote ni binaire Intel n’est redistribué. Licence GNU GPLv3 (`GPL-3.0-only`), texte intégral dans `LICENSE`.

Une mesure de développement en lecture seule, sur 120 requêtes espacées de 250 ms, a donné environ 7,1 Mo de mémoire résidente maximale, 1,3 Mo de mémoire privée, 0,5 % d’un cœur logique et une latence médiane de 1,6 ms (95e percentile 2,0 ms, maximum 40,7 ms). Ces valeurs concernent le processus moteur, hors client Python, avec le processus de récupération inactif ; elles ne constituent ni une comparaison avec Python ni un test d’endurance.
