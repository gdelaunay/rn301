# tools/ — Scripts de reverse engineering et de probe

Ces scripts sont utilisés pendant le développement du projet. Ils ne font **pas**
partie du package `rn301` (100 % stdlib) et nécessitent des dépendances externes
pour certains.

## Dépendances

| Script | Dépendances |
|---|---|
| `analyze_apk.py`, `decompile.py` | `androguard`, `loguru` |
| `extract_ops.py` | (aucune — lit un fichier Java décompilé) |
| `probe_gena.py`, `probe_gena_raw.py`, `probe_gets.py`, `scan_remote.py`, `test_push.py` | (aucune — 100 % stdlib) |

Installation pour les scripts APK :
```bash
pip install androguard loguru
```

## Scripts

- **`analyze_apk.py`** — Extrait les strings et classes du DEX de l'APK NP Controller.
  Sortie : `apk/decompiled/strings_*.txt`, `classes.txt`.
- **`decompile.py`** — Recherche de références et décompilation ciblée de classes Java.
  Sortie : `apk/decompiled/src/`.
- **`extract_ops.py`** — Produit la table des opérations YNC depuis `q.java` décompilé.
  Sortie : `docs/ops_brut.txt`.
- **`probe_gena.py`** — Sonde READ-ONLY le renderer UPnP (SCPD, capacités, abonnement GENA).
- **`probe_gena_raw.py`** — Logge les bodies NOTIFY GENA en parallèle d'un polling YNC
  (corrélation events vs. état).
- **`probe_gets.py`** — Teste les GET YNC un par un, valide les chemins.
  Sortie : `amp/probe_gets.json`.
- **`scan_remote.py`** — Brute-force les 256 codes `Remote_Signal` (device 7F01/7A01).
  Sortie : `amp/remote_scan*.json`.
- **`test_push.py`** — Test standalone du push DLNA (serveur HTTP + SOAP SetAVTransportURI).

## Prérequis matériels

Les scripts de probe (`probe_*`, `scan_remote`, `test_push`) nécessitent un R-N301
accessible sur le réseau local. L'IP est configurée en dur (`192.168.1.91`) ou passée
en argument — adapter selon son réseau.

`analyze_apk.py` et `decompile.py` nécessitent l'APK NP Controller v4.60 (non inclus,
fichier sous copyright Yamaha).
