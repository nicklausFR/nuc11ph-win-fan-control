# NUC11phi7 Cooling — interface Qt dans le systray

[English](README.md) · [Documentation du projet](../README.fr.md)

Panneau Python / PySide6 pour Windows x64 : températures CPU/GPU/SSD,
charges CPU et GPU NVIDIA, vitesses des ventilateurs, profils de refroidissement
et ventilation forcée. Interface en français ou en anglais, thèmes clair/sombre/automatique.

Les vignettes de température et de charge apparaissent en premier : CPU,
GPU NVIDIA, GPU intégré et chaque SSD. La charge apparaît uniquement pour les
composants dont elle est disponible. La section **Ventilation** vient
ensuite et regroupe le profil, la consigne unique et une seule barre de vitesse.
La valeur en tr/min est la moyenne des relevés CPU/GPU disponibles ; les valeurs
exactes et l’échelle d’affichage automatique restent dans l’infobulle. Cette
échelle est une plage d’affichage, pas un maximum matériel mesuré.
Les sondes SMART supplémentaires d’un SSD restent accessibles dans son infobulle.

## Lancement

Depuis la racine du projet, avec Python x64 ≥ 3.11 :

```powershell
python -m pip install -r ui/requirements.txt
python ui/app.py --show
```

Le programme utilise le moteur existant ou lance `nuc-engine.exe serve --allow-write`
sans console, depuis `fan-control/build/` ou à côté de l’exécutable distribué.
Cette autorisation ne sélectionne aucun mode : une commande nécessite un clic sur
**Appliquer**. Le moteur vérifie la compatibilité matérielle et ses garde-fous.

```powershell
python ui/app.py --engine "C:\chemin\nuc-engine.exe" --show
python ui/app.py --language en --show
python ui/app.py --no-start
python ui/app.py --read-only
```

`--no-start` utilise uniquement un moteur existant. `--read-only` désactive les
commandes et lance, si nécessaire, un moteur en lecture seule. Un chemin explicite
inexistant est signalé sans substitution silencieuse.

Un moteur externe démarré sans `--allow-write` reste en lecture seule : l’interface
ne le tue pas pour lui substituer un autre processus. Le relancer explicitement
avec `--allow-write` pour activer les commandes. Une session appartenant à une
autre application n’est ni remplacée ni restaurée par cette interface.

## Utilisation

- Clic sur l’icône : afficher ou masquer le panneau près du systray.
- Clic extérieur : masquer, sauf si le panneau est épinglé.
- Échap ou clic sur l’icône du systray : masquer.
- Option Épingler du menu : garder visible au premier plan.
- Clic droit ou ⋮ : options, apparence, actualisation et sortie.
- Menu **Langue** : **Français**, **English** ou **Langue de Windows**.
- Survol de l’icône : températures, charges, vitesses et mode actuel.
- Actualisation : 1, 2 ou 5 secondes ; 2 secondes par défaut.
- Survol d’un SSD : identifiant et sondes SMART supplémentaires.

Masquer ou fermer la fenêtre laisse l’application active. Utiliser **Quitter**
pour arrêter. Préférences dans `QSettings`, sous `nicklausFR/NucCooling`.

La langue change immédiatement et le choix est mémorisé. Par défaut, la langue
de Windows donne le français pour une configuration française et l’anglais sinon.
`--language fr|en|auto` remplace la préférence pour ce lancement ; un choix dans
le menu met à jour la préférence enregistrée. Le changement de langue conserve
la sélection, la saisie du pourcentage et la session de ventilation active.
Les détails de diagnostic inconnus restent tels que rapportés par le pilote.

## Profils et ventilation forcée

Choisir **Silencieux**, **Équilibré** ou **Refroidissement**, puis **Appliquer**.
Ces profils OEM gardent leur régulation automatique par le firmware.

Choisir **Vitesse forcée**, régler la consigne entre **50 et 99 %**, puis
**Appliquer**. C’est un pourcentage du canal commun aux ventilateurs CPU et GPU,
pas une consigne en tr/min. Les deux relevés exacts restent dans l’infobulle de vitesse.
Le moteur ne propose pas de réglage indépendant ou d’arrêt des ventilateurs.

**Rétablir l’automatique initial** restaure la configuration sauvegardée avant
la session. Changer la sélection ou déplacer le curseur n’envoie aucune commande.
Les réglages matériels ne sont pas réappliqués automatiquement au démarrage.

Une valeur modifiée reste indiquée **À appliquer** jusqu’au clic sur **Appliquer**.
Pendant **Envoi…**, le curseur et le champ restent éditables ; une nouvelle valeur
n’écrase pas la commande déjà envoyée. Elle peut être appliquée à son tour après
confirmation. Les actualisations ne remplacent pas une saisie en cours.
Le panneau adapte sa hauteur au contenu et défile sur un écran trop petit,
sans comprimer les contrôles.

Chaque commande utilise une session de **10 secondes**, renouvelée toutes les
**2 secondes**, même lorsque le panneau est masqué et indépendamment de la cadence
d’affichage. La sortie normale restaure la session possédée. Le moteur C et son
processus de récupération gèrent aussi l’expiration et la disparition de l’interface.
Les limites et garanties de récupération restent celles décrites à la racine.

En vitesse forcée, une température CPU ≥ **85 °C**, GPU NVIDIA ≥ **80 °C**,
une sonde indisponible ou des tr/min invalides refusent/interrompent la commande.
L’interface affiche le retour automatique et ne réapplique pas la consigne.
Le profil OEM **Refroidissement** reste soumis à sa propre régulation firmware.

## Mesures

La charge CPU est le pourcentage de temps occupé, calculé à partir des compteurs
Windows `GetSystemTimes` entre deux relevés ; le premier relevé affiche « — ».
La charge GPU NVIDIA vient de `nvmlDeviceGetUtilizationRates` dans la bibliothèque
NVML du pilote déjà installé. Aucun pilote ou DLL NVIDIA n’est redistribué.
Les périodes et définitions des compteurs peuvent différer du Gestionnaire des
tâches. La charge du GPU intégré n’est pas incluse dans celle du GPU NVIDIA.
Un compteur indisponible affiche « — », distinct d’une charge réelle de 0 %.

Le mode vient de `telemetry.control.mode`. En automatique, le libellé du profil
est une correspondance des deux seuils encodés et des deux minimums de ventilation
avec une seule entrée du catalogue `profiles()`, pas une vérification de tous les
octets OEM. Les seuils encodés ne sont pas présentés comme des degrés Celsius.

Les SSD utilisent la température SMART composite (index 0), avec cache moteur de
10 secondes. Les valeurs indisponibles affichent « — ». La reconnexion est
automatique, avec effacement des valeurs périmées. Un moteur de test affiche
explicitement **SIMULATION**. Les compteurs CPU/GPU ont leur propre thread,
distinct des commandes et du renouvellement. Une lecture NVIDIA lente ne retarde
donc pas le contrôle des ventilateurs. Les commandes attendent jusqu’à 8 secondes
par échange IPC pour couvrir la confirmation de 5 secondes prévue par le moteur.
Une réponse perdue déclenche une tentative de restauration uniquement si le
moteur confirme que la session appartient à cette instance.

## Exécutable et validation

```powershell
python -m pip install -r ui/requirements-build.txt
python tools/build_ui.py
python -m unittest discover -s tests -p "test_*ui*.py" -v
python -m unittest discover -s tests -p test_i18n.py -v
python -m unittest discover -s tests -p test_cooling_controls.py -v
python -m unittest discover -s tests -p test_editing_responsiveness.py -v
python tools/verify_ui.py
```

Lancer `dist/nuc-cooling.exe` et conserver `dist/nuc-engine.exe` à côté.
`verify_ui.py` ne sélectionne aucun mode : trois lectures, vérification des octets
EC et de la sortie du thread, captures française/anglaise claire/sombre et commandes forcées dans
`test-results/ui/`.

`python tools/verify_cooling_controls.py` nécessite le moteur simulé compilé et
l’absence d’un autre moteur. Il refuse les commandes si `simulated` n’est pas
vrai. Il vérifie les trois profils, le maintien d’une consigne au-delà de sa durée
initiale, le retour thermique sans réapplication et la restauration à la fermeture.

Sources sous GPL-3.0-only, comme le moteur.
