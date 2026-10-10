"""Analyse l'APK Network Player Controller : strings du dex + classes.

Usage:
    .venv/Scripts/python tools/analyze_apk.py strings   -> dump tous les strings du dex
    .venv/Scripts/python tools/analyze_apk.py classes   -> liste les classes
"""
import sys, os, re, logging
from loguru import logger

logger.remove()  # couper le debug spam d'androguard

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK = os.path.join(BASE, "NETWORK PLAYER CONTROLLER_4.60_APKPure.apk")
OUT = os.path.join(BASE, "apk", "decompiled")
os.makedirs(OUT, exist_ok=True)

from androguard.misc import AnalyzeAPK

def load():
    a, d, dx = AnalyzeAPK(APK)
    return a, d, dx

def dump_strings():
    a, d, dx = load()
    dex = d[0]
    strings = sorted({(s.get_value() if hasattr(s, "get_value") else str(s)) for s in dex.get_strings()})
    with open(os.path.join(OUT, "strings_all.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(strings))
    print(f"{len(strings)} strings -> apk/decompiled/strings_all.txt")

    # filtrage YNC
    pat = re.compile(r"(YAMAHA_AV|GetParam|<Put>|<Get>|Main_Zone|Net_Radio|Sound_Video|Balance|Spkr|Tone|YamahaRemoteControl|desc\.xml)", re.I)
    ync = [s for s in strings if pat.search(s)]
    with open(os.path.join(OUT, "strings_ync.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(ync))
    print(f"{len(ync)} strings YNC -> apk/decompiled/strings_ync.txt")

def dump_classes():
    a, d, dx = load()
    dex = d[0]
    classes = sorted(c.get_name() for c in dex.get_classes())
    with open(os.path.join(OUT, "classes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(classes))
    print(f"{len(classes)} classes -> apk/decompiled/classes.txt")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "strings"
    {"strings": dump_strings, "classes": dump_classes}[cmd]()
