"""Extrait la table des opérations YNC depuis le q.java décompilé.

Chaque `case 3019899xx:` construit un chemin XML (via v0_1.a.add("Tag"))
et parfois une valeur (v9_1 = "..."). Ce script produit une table lisible.

Usage: .venv/Scripts/python tools/extract_ops.py > docs/ops_brut.txt
"""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "apk", "decompiled", "src", "com", "yamaha", "npcontroller", "j", "q.java")

raw = open(SRC, encoding="utf-8").read()

# découper en blocs case
parts = re.split(r'case (3019899\d{2}):', raw)
print("# Table des opérations YNC (extrait de q.java)")
print("# format: op -> chemin XML | valeurs\n")
for i in range(1, len(parts), 2):
    op, body = parts[i], parts[i + 1]
    # limiter au bloc (jusqu'au prochain case/break majeur — heuristique: 40 lignes)
    lines = body.splitlines()[:45]
    body = "\n".join(lines)
    tags = re.findall(r'\.a\.add\("([^"]+)"\)', body)
    vals = re.findall(r'v9_1 = ([^;]+);', body)
    vals = [v.strip() for v in vals if 'StringBuilder' not in v]
    dyn = re.findall(r'v3_\d+ = this\.b\(this\.a\)', body)
    chemin = "/".join(tags) if tags else "?"
    if dyn:
        chemin = "<zone_courante>/" + chemin
    print(f"op {op}:")
    print(f"  chemin: {chemin}")
    if vals:
        print(f"  valeurs: {vals[:8]}")
    # chaînes diverses dans le bloc
    misc = re.findall(r'"([A-Za-z_ ]{3,30})"', body)
    misc = [m for m in misc if m not in tags and m not in ("GetParam",)]
    if misc:
        print(f"  autres strings: {sorted(set(misc))[:10]}")
    print()
