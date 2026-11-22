# API locale v1

Transport : canal nommé Win32 `\\.\pipe\NucControl.v1.<SID du propriétaire>`, octets, duplex, clients distants refusés, une connexion par appel. ACL limitée au propriétaire et à SYSTEM. Les privilèges du service ne sont jamais transférés au client.

Les entiers sont non signés, little-endian. Les structures C sont vérifiées par `_Static_assert` et le client utilise `struct` ; aucun pointeur n’est transmis dans le protocole public.

Requête : **48 octets**, format Python `<8I16s`.

| Offset | Taille | Champ |
| --- | --- | --- |
| 0 | 4 | Magic `0x3143554E` (`NUC1`) |
| 4 | 4 | Version `1` |
| 8 | 4 | Taille `48` |
| 12 | 4 | Commande |
| 16 | 16 | Quatre valeurs uint32 |
| 32 | 16 | Identifiant de session ; zéro avant sa création |

| Commande | Valeurs |
| --- | --- |
| 1 — telemetry | toutes nulles |
| 2 — capabilities | toutes nulles |
| 3 — profiles | toutes nulles ; identifiant nul |
| 4 — safeguards | toutes nulles ; identifiant nul |
| 10 — duty | pourcentage 50..99, durée 1..15 s, 0, 0 |
| 11 — profile | quiet=0 / balanced=1 / cool=2, durée, 0, 0 |
| 12 — thresholds | minimum primaire encodé, minimum secondaire encodé, durée, 0 |
| 13 — renew | durée, 0, 0, 0 ; identifiant actif obligatoire |
| 14 — restore | toutes nulles ; identifiant nul |

Pour mettre à jour une session existante, ses commandes doivent venir du même processus client et fournir son identifiant actif. L’identifiant est renvoyé sous forme hexadécimale dans `control.active_lease`. Le SDK s’en occupe. Une session terminée peut être suivie d’une nouvelle session.

Réponse : en-tête de **32 octets**, `<8I` : magic, version, taille d’en-tête=32, longueur JSON, code d’état, trois entiers réservés nuls. Le JSON UTF-8 est limité à 8 192 octets. État 0 : succès. Sinon : `{"error":{"code":"…","win32":0,"message":"…"}}`. Après lecture complète du corps, le client envoie l’octet `AC` et ferme la connexion. Les entrées incomplètes et les clients qui ne lisent pas leurs réponses ont un délai borné.

`telemetry` contient les températures identifiées, les tr/min, les températures NVMe, les seuils encodés, les canaux manuels, le contenu EC brut en hexadécimal pour le diagnostic, l’état du contrôle et les compteurs CPU/mémoire du processus principal. Les mesures successives peuvent évoluer entre les lectures ; ce n’est pas une capture atomique de tout le matériel.

`control.last_restoration.restored=false` avec `reason=none` signifie qu’aucune restauration n’a encore été effectuée par cette instance, pas un échec. `mode=external_manual` signifie qu’un mode manuel extérieur a été détecté sans sauvegarde de cette API ; le moteur refuse alors une nouvelle session et ne devine pas une configuration à restaurer.

Depuis le moteur 0.3.0, `capabilities.discovery` annonce les commandes `profiles` et `safeguards`. Le protocole reste en version 1 ; les anciennes commandes restent inchangées. Toutes les valeurs et l’identifiant de session doivent être nuls pour consulter ces catalogues. Ils sont disponibles en lecture seule, sans sélection de profil, écriture matérielle ni prise de possession d’une session active.

La commande 3 répond avec `{"schema_version":1,"profiles":[…]}`. Le SDK Python `nuc.profiles()` renvoie directement la liste. Chaque profil contient `id`, `label`, `source`, `regulation`, `minimum_duty_percent` (CPU et NVIDIA) et `thresholds` (unités encodées, conversion Celsius non validée). Ces valeurs proviennent des profils d’écriture réels du moteur, pas d’une copie de paramètres dans l’interface.

La commande 4 et `nuc.safeguards()` renvoient un objet avec `schema_version`, `write_enabled`, `configurable_via_api=false`, `limits`, `rules`, `limitations` et `validation`. Il s’agit de la politique configurée, pas de l’état de santé actuel des capteurs. `rules[].applies_to` précise les modes concernés ; les limites à 85 °C CPU / 80 °C GPU concernent uniquement les consignes directes et seuils personnalisés. Les bornes exposées sont issues des mêmes constantes C que celles utilisées pour refuser ou restaurer les commandes. Les profils automatiques OEM gardent leur régulation firmware.

Un garde-fou peut avoir les actions `reject`, `restore`, `reject_or_restore`, `serialize`, `verify`, `retry_restore` ou `restrict_access`. `limit_ref`, s’il est présent, désigne une clé de `limits`. Le catalogue ne permet ni de désactiver une règle ni de modifier ses bornes. Les limites de surveillance et les validations encore manquantes sont explicites dans `limitations` et `validation`.

L’interface du pilote Intel et les limites de validation matérielle sont décrites dans le document `PROTOCOL.md` conservé avec les sources. Cette API publique n’expose que les fonctions validées, pas les numéros IOCTL ni les formats de commande bruts.
