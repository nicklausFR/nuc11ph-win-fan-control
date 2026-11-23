# SPDX-License-Identifier: GPL-3.0-only
"""French/English presentation strings translated at the UI boundary.

Each window owns its translator. Switching language never changes worker state.
French strings are also the stable message keys used by existing worker signals.
"""
from __future__ import annotations

from functools import lru_cache
import re
from string import Formatter

from PySide6.QtCore import QLocale


ENGLISH = {
    "Charge : —": "Load: —",
    "Charge : {value:.0f} %": "Load: {value:.0f} %",
    "connexion en cours": "connecting",
    "VENTILATION": "FAN CONTROL",
    "Vitesse :": "Speed:",
    "Connexion en cours…": "Connecting…",
    "VENTILATEUR CPU": "CPU FAN",
    "VENTILATEUR GPU": "GPU FAN",
    "{fan} — vitesse mesurée": "{fan} — measured speed",
    "Vitesse mesurée en tr/min ; la consigne est commune aux deux ventilateurs.":
        "Measured speed in RPM; both fans share one duty setting.",
    "Mode à appliquer": "Mode to apply",
    "Vitesse forcée commune": "Shared forced speed",
    "Appliquer": "Apply",
    "Envoi…": "Applying…",
    "Consigne commune des ventilateurs": "Shared fan duty",
    "Consigne commune de ventilation en pourcentage": "Shared fan duty in percent",
    "Consigne commune CPU + GPU · 50–99 %": "Shared CPU + GPU duty · 50–99 %",
    "Rétablir l’automatique initial": "Restore original automatic control",
    "Connexion au moteur…": "Connecting to engine…",
    "Capteurs de température et de charge": "Temperature and load sensors",
    "Processeur": "Processor",
    "Carte graphique": "Graphics card",
    "GPU NVIDIA": "NVIDIA GPU",
    "GPU INTÉGRÉ": "INTEGRATED GPU",
    "Graphique intégré": "Integrated graphics",
    "GPU intégré": "Integrated GPU",
    "Système 1": "System 1",
    "Système 2": "System 2",
    "Pompe": "Pump",
    "Épingler au premier plan": "Keep panel on top",
    "Apparence": "Appearance",
    "Automatique (Windows)": "Automatic (Windows)",
    "Clair": "Light",
    "Sombre": "Dark",
    "Actualisation": "Refresh interval",
    "{seconds} seconde": "{seconds} second",
    "{seconds} secondes": "{seconds} seconds",
    "Langue": "Language",
    "Langue de Windows": "Windows language",
    "Quitter": "Quit",
    "Retour auto dès CPU {cpu_limit} °C / GPU {gpu_limit} °C":
        "Auto restore at CPU {cpu_limit} °C / GPU {gpu_limit} °C",
    "Surveillance seule : commandes désactivées.": "Monitoring only: controls disabled.",
    "Moteur en lecture seule : relancer avec --allow-write pour activer les commandes.":
        "Read-only engine: restart with --allow-write to enable controls.",
    "Une autre application contrôle le refroidissement.": "Another application controls cooling.",
    "Application de la commande…": "Applying command…",
    "À appliquer : vitesse forcée à {percent} %.": "Pending: forced speed at {percent} %.",
    "Commande maintenue tant que l’application reste ouverte.":
        "Control stays active while the application is running.",
    "Dernier relevé : {time}\nMoteur {version} · Relevé toutes les {seconds} s\nTempératures SSD mises en cache 10 s par le moteur":
        "Last reading: {time}\nEngine {version} · Readings every {seconds} s\nSSD temperatures cached by the engine for 10 s",
    "Ventilation": "Fan control",
    "Vitesse de ventilation": "Fan speed",
    "Ventilation commune : moyenne des relevés disponibles.\nCPU : {cpu} · GPU : {gpu}\nÉchelle d’affichage automatique : 0–{maximum}.":
        "Shared cooling: average of available readings.\nCPU: {cpu} · GPU: {gpu}\nAutomatic display scale: 0–{maximum}.",
    "Ventilation : {rpm}": "Cooling: {rpm}",
    "Ventilateurs : CPU {cpu} · GPU {gpu}": "Fans: CPU {cpu} · GPU {gpu}",
    "Charge CPU moyenne depuis le précédent relevé": "Average CPU load since the previous reading",
    "Occupation du GPU NVIDIA mesurée par NVML": "NVIDIA GPU utilization reported by NVML",
    "Commandes indisponibles pendant la déconnexion.": "Controls unavailable while disconnected.",
    "Indisponible": "Unavailable",
    "Déconnecté\nDernier relevé : {time}": "Disconnected\nLast reading: {time}",
    "aucun": "none",
    "déconnecté": "disconnected",
    "Manuel": "Manual",
    "Commande extérieure": "External control",
    "Consigne temporaire du moteur": "Temporary engine duty setting",
    "Seuils personnalisés": "Custom thresholds",
    "Régulation automatique du firmware": "Automatic firmware regulation",
    "Restauration en cours": "Restoring automatic control",
    "Retour à la configuration sauvegardée": "Returning to the saved configuration",
    "Mode inconnu": "Unknown mode",
    "État non reconnu par cette version de l’interface": "State not recognized by this interface version",
    "Automatique": "Automatic",
    "Profil correspondant aux paramètres exposés par le moteur": "Profile matching the parameters reported by the engine",
    "Configuration personnalisée ou profil non identifié": "Custom configuration or unidentified profile",
    "Silencieux": "Quiet",
    "Équilibré": "Balanced",
    "Refroidissement": "Cooling",
    "Sonde {index} : {temperature}": "Sensor {index}: {temperature}",
    "Température SMART composite": "SMART composite temperature",
    "Le moteur est en lecture seule. Relancez-le avec --allow-write pour commander le refroidissement.":
        "The engine is read-only. Restart it with --allow-write to control cooling.",
    "Retour automatique : température CPU ≥ 85 °C, GPU ≥ 80 °C ou sonde indisponible.":
        "Automatic restore: CPU ≥ 85 °C, GPU ≥ 80 °C or unavailable temperature sensor.",
    "Retour automatique : lecture d’un ventilateur indisponible.": "Automatic restore: fan reading unavailable.",
    "Une autre application contrôle déjà le refroidissement.": "Another application already controls cooling.",
    "La commande a expiré ; la configuration automatique est rétablie.": "The command expired; automatic control has been restored.",
    "Le moteur n’a pas confirmé la commande à temps. Vérifiez le mode actif avant de réessayer.":
        "The engine did not confirm the command in time. Check the active mode before trying again.",
    "Moteur arrêté. Démarrez nuc-engine.exe serve, puis réessayez.": "Engine stopped. Start nuc-engine.exe serve, then try again.",
    "Moteur introuvable : {path}. Utilisez --engine pour indiquer nuc-engine.exe.":
        "Engine not found: {path}. Use --engine to specify nuc-engine.exe.",
    "Moteur inaccessible. Nouvelle tentative dans quelques secondes.": "Engine unavailable. Retrying in a few seconds.",
    "Le moteur démarre ou ne répond plus.": "The engine is starting or no longer responding.",
    "Arrêt demandé": "Shutdown requested",
    "Le moteur s’est arrêté au démarrage.": "The engine stopped during startup.",
    "Le moteur n’a pas répondu dans les cinq secondes.": "The engine did not respond within five seconds.",
    "Le contrôle a pris fin ; aucune commande n’est réappliquée.": "Control ended; no command will be reapplied.",
    "Le moteur est en lecture seule": "The engine is read-only",
    "Profil inconnu": "Unknown profile",
    "Commande inconnue": "Unknown command",
    "La prise de contrôle n’a pas été confirmée par le moteur.": "The engine did not confirm control ownership.",
    "La session appartient à une autre application.": "The session belongs to another application.",
    "Configuration automatique initiale rétablie.": "Original automatic configuration restored.",
    "Compteurs de charge CPU Windows indisponibles": "Windows CPU load counters unavailable",
    "NVML NVIDIA indisponible : pilote NVIDIA requis": "NVIDIA NVML unavailable: NVIDIA driver required",
    "Charge GPU indisponible (NVML {code})": "GPU load unavailable (NVML {code})",
    "Compteur indisponible ; nouvelle tentative automatique": "Counter unavailable; retrying automatically",
}


def resolve_language(preference="auto", *, system_locale=None):
    if preference in ("fr", "en"):
        return preference
    locale = system_locale if system_locale is not None else QLocale.system().name()
    return "fr" if locale.lower().split("_")[0].split("-")[0] == "fr" else "en"


@lru_cache(maxsize=1)
def _templates():
    patterns = []
    for source, target in ENGLISH.items():
        parts = list(Formatter().parse(source))
        if not any(field is not None for _, field, _, _ in parts):
            continue
        pattern = "".join(re.escape(literal) + (f"(?P<{field}>.+?)" if field is not None else "")
                          for literal, field, _, _ in parts)
        patterns.append((re.compile(pattern, re.DOTALL), target))
    return patterns


class Translator:
    def __init__(self, language):
        self.language = resolve_language(language)

    def __call__(self, source, **values):
        target = ENGLISH.get(source, source) if self.language == "en" else source
        return target.format(**values) if values else target

    def message(self, source):
        """Localize worker messages at display time, including known parameters.

        Unknown diagnostic details are preserved verbatim rather than guessed.
        """
        if self.language != "en" or source in ENGLISH:
            return self(source)
        for pattern, target in _templates():
            match = pattern.fullmatch(source)
            if match:
                return "".join(literal + (match[field] if field is not None else "")
                               for literal, field, _, _ in Formatter().parse(target))
        return source


def profile_name(profile, language="fr"):
    name = {"quiet": "Silencieux", "balanced": "Équilibré", "cool": "Refroidissement"}.get(
        profile["id"], profile.get("label", profile["id"]))
    return Translator(language)(name)
