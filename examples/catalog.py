# SPDX-License-Identifier: GPL-3.0-only
"""Afficher les profils et garde-fous. Le moteur doit déjà être lancé."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"python"))
from nuc_client import NucClient


if __name__=="__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with NucClient() as nuc:
        print("PROFILS")
        print(json.dumps(nuc.profiles(),ensure_ascii=False,indent=2))
        print("GARDE-FOUS")
        print(json.dumps(nuc.safeguards(),ensure_ascii=False,indent=2))
