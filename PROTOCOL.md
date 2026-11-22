# Interface vérifiée localement

Ces informations décrivent le NUC11PHi7 / NUC11PHBi7 et la version exacte du pilote inscrits dans `fan-control/src/hardware.c`. Ce n’est pas une spécification publique Intel/ASUS. Les correspondances proviennent de l’analyse statique de `PerformanceTuningLibrary.dll`, de lectures natives et d’essais avec relecture et restauration.

Interface SetupAPI : GUID `1acd3a40-c4db-4542-9c73-9898b0d8ff6e`, périphérique `ACPI\INTC1036`, service `PerformanceDriver`. Les transactions partagent le mutex OEM `Global\Access_ISABUS.HTP.Method`.

| IOCTL | Entrée | Sortie | Usage |
| --- | --- | --- | --- |
| `0x9C406020` | vide | 94 octets | Configuration EC |
| `0x9C406024` | `02` | 18 octets | Tr/min |
| `0x9C406048` | vide | 15 octets | Températures |
| `0x9C40A01C` | profil 90 octets | vide | Configuration automatique |
| `0x9C40A040` | `02 01 <pourcentage>` | vide | Consigne directe du canal partagé confirmé |

Configuration EC : six blocs de 14 octets, espacés de 16 octets. Les deux octets réservés entre blocs sont préservés ; le dernier bloc n’a pas de réserve finale. Le profil d’écriture a trois octets d’en-tête (`00 01 1A` automatique, `00 01 1B` pour les seuils personnalisés observés), six blocs contigus de 14 octets et trois octets réservés finaux. Confondre les deux dispositions décale les canaux suivants.

Dans un bloc, les positions 0/6 sont les sources thermiques, 1/7 les maximums encodés, 2/8 les minimums encodés, 3/9 les pourcentages minimums, 4/10 les incréments, 5/11 les paramètres d’arrêt. L’octet 12 porte le bit manuel `0x80` et les sept bits de consigne. L’octet 13 est conservé. Pour le canal 1, 60 % se relit donc `0xBC` à l’offset EC 12. La conversion des seuils encodés en °C n’est pas démontrée.

La commande avec canal 0 ne modifiait pas le bit manuel attendu. Le canal 2 acceptait un bit manuel à l’offset 28 sans réponse physique distincte démontrée. Seul le canal 1 partagé est exposé ; l’essai antérieur à 75 % avait fait monter les deux ventilateurs vers 2 500 tr/min.

Tr/min : six enregistrements de trois octets, type puis entier non signé 16 bits big-endian. Types reconnus : 1 CPU, 2 GPU NVIDIA, 3/4 système, 5 pompe, 7 second CPU.

Températures : octet 0 GPU NVIDIA, octet 1 CPU, octet 2 GPU intégré, en °C selon les fonctions OEM identifiées. Les douze autres octets ne sont pas interprétés. Les valeurs nulles ou hors plage sont déclarées indisponibles.

SSD : interface Windows `\\.\PhysicalDriveN`, accès zéro, partage lecture/écriture. `IOCTL_STORAGE_QUERY_PROPERTY` (`0x002D1400`) avec propriété 50, protocole NVMe 3, type log page 2, page SMART 2, offset protocole 40 et longueur 512. Le descripteur de réponse de 48 octets et les limites sont contrôlés. Température composite à l’offset 1 du journal, huit sondes à l’offset 200, entiers 16 bits little-endian en kelvins, conversion entière par soustraction de 273. La propriété de température générique marchait seulement sur un des deux SSD ; le journal SMART fonctionne sur les deux.

Le projet GitHub partagé pendant la recherche, `ppundsh/openhardwaremonitorlib-fix` (commit `485b1711956f97608272a5a6f1ffde1a6c8d9546`), a aidé à repérer cette voie d’accès. Aucun de ses composants binaires n’est embarqué.
