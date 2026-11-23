# SPDX-License-Identifier: GPL-3.0-only
"""Presentation of engine telemetry. This module never accesses hardware."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from i18n import Translator, profile_name

NAMES = {"cpu": "CPU", "nvidia_gpu": "GPU NVIDIA", "integrated_gpu": "GPU intégré",
         "system_1": "Système 1", "system_2": "Système 2", "pump": "Pompe", "cpu_2": "CPU 2"}


def number(value):
    return value if type(value) in (int, float) and isfinite(value) else None


def temperature(sensor):
    value = number(sensor.get("celsius"))
    return value if sensor.get("available", True) and value is not None and -50 <= value <= 125 else None


def format_temperature(value):
    return f"{value:g} °C" if value is not None else "—"


def format_rpm(value, language="fr"):
    if value is None:
        return "—"
    formatted = f"{value:,.0f}"
    return formatted + " RPM" if language == "en" else formatted.replace(",", "\u202f") + " tr/min"


@dataclass(frozen=True)
class CoolingMode:
    title: str
    detail: str


def cooling_mode(status, profiles, language="fr"):
    tr = Translator(language)
    mode = status.get("control", {}).get("mode")
    if mode in ("duty", "external_manual"):
        duties = [number(c.get("duty_percent")) for c in status.get("manual_channels", [])]
        values = " / ".join(f"{v:g} %" for v in duties if v is not None)
        return CoolingMode(tr("Manuel") + (f" · {values}" if values else ""),
                           tr("Commande extérieure" if mode == "external_manual" else "Consigne temporaire du moteur"))
    if mode == "thresholds":
        return CoolingMode(tr("Seuils personnalisés"), tr("Régulation automatique du firmware"))
    if mode == "recovering":
        return CoolingMode(tr("Restauration en cours"), tr("Retour à la configuration sauvegardée"))
    if mode not in ("automatic", "profile"):
        return CoolingMode(tr("Mode inconnu"), tr("État non reconnu par cette version de l’interface"))
    thresholds = status.get("thresholds", {})
    matches = []
    for profile in profiles:
        expected = profile.get("thresholds", {})
        minimums = profile.get("minimum_duty_percent", {})
        pairs = [(thresholds.get(key), expected.get(key))
                 for key in ("primary_min_encoded", "secondary_min_encoded")]
        pairs += [(thresholds.get("primary_min_duty_percent"), minimums.get("cpu")),
                  (thresholds.get("secondary_min_duty_percent"), minimums.get("nvidia_gpu"))]
        if all(actual is not None and actual == wanted for actual, wanted in pairs):
            matches.append(profile_name(profile, language))
    if len(matches) == 1:
        return CoolingMode(tr("Automatique") + f" · {matches[0]}", tr("Profil correspondant aux paramètres exposés par le moteur"))
    return CoolingMode(tr("Automatique"), tr("Configuration personnalisée ou profil non identifié"))


def storage_rows(status, language="fr"):
    tr = Translator(language)
    rows = []
    for disk in status.get("ssds", []):
        composite = next((s for s in disk.get("sensors", []) if s.get("index") == 0), {})
        value = temperature(composite) if disk.get("available") else None
        details = [tr("Sonde {index} : {temperature}", index=s.get('index'), temperature=format_temperature(temperature(s)))
                   for s in disk.get("sensors", []) if s.get("index") != 0]
        rows.append((disk.get("id", "ssd"), disk.get("model", "SSD NVMe"), value,
                     "\n".join([disk.get("id", "SSD NVMe"), tr("Température SMART composite"), *details])))
    return rows
