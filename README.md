# rn301 — Contrôle complet d'un Yamaha R-N301 (Python + web)

Français · [English](README.en.md)

[![CI](https://github.com/gdelaunay/rn301/actions/workflows/ci.yml/badge.svg)](https://github.com/gdelaunay/rn301/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.12%20|%203.13-blue)](#démarrage-rapide)

Librairie Python et serveur web pour piloter un ampli hi-fi **Yamaha R-N301**
(2013) sans l'application officielle. Protocole **YNC** (Yamaha Network Control)
reverse-engineered à partir de l'APK *NP Controller 4.60* + validation live
commande par commande sur le firmware 1.13/0.05.

**100 % stdlib** — zéro dépendance.

Le R-N301 ne publie **aucun descripteur YNC** (`desc.xml` → 404) : les librairies
YNC génériques, qui découvrent les commandes via ce fichier, sont incapables de
fonctionner avec lui. Les ~90 commandes ont donc été extraites en dur de
l'APK *NP Controller* puis validées en live. 
  
Le projet couvre les **trois canaux**
de l'ampli — YNC (`:80`), MediaRenderer UPnP (`:8080`) et injection IR
`Remote_Signal` — et exploite le MediaRenderer pour **pousser de l'audio** vers
l'ampli (module *localfilecontroller* de l'app officielle).

```
                             ┌────────────────┐
                             │     rn301/     │
                             │   (library)    │
                             └───┬────────▲───┘
                HTTP + XML (YNC) │        │ UPnP/SOAP + GENA + HTTP
                             :80 │        │ :8080 (+ file server)
                         ┌───────▼────────▼──────┐
                         │      Yamaha R-N301     │
                         │      192.168.1.XX      │
                         │    (SSDP discovered)   │
                         └────────────────────────┘
```

> Développé avec Qwen3.8 Max et Qwen3.8 Flash via [Pi](https://github.com/badlogic/pi-mono), dans [Zed](https://zed.dev) via [ACP](https://agentclientprotocol.com).

## Démarrage rapide

```bash
# dans ce repo (aucune installation nécessaire)
python -c "from rn301 import discover_one, RN301; amp=RN301(discover_one()['host']); amp.power_on()"

# serveur web → http://localhost:8080
python -m webapp.server --port 8080 --amp 192.168.1.91

# tests hors-ligne (aucun ampli requis)
python -m unittest discover tests
```

Installer la lib dans un autre projet :

```bash
pip install -e ../rn301          # ou : uv add ../rn301 --editable
```

Le package expose deux CLI : `rn301-discover` (scan SSDP → JSON) et
`rn301-gena` (écoute live des events GENA). Type hints inclus (`py.typed`).

## Librairie `rn301/`

Client YNC complet, ~90 opérations validées en live. Gère les quirks firmware :
requête unique par POST, retry sur réponses vides/400, délai 5 s sur RC=3
(ampli "occupé" après changement de source réseau).

```python
from rn301 import RN301, discover_one
amp = RN301(discover_one()["host"])

amp.power_on(); amp.set_volume(25); amp.set_mute(True)
amp.set_input("SERVER")                    # 11 sources disponibles
amp.playback("SERVER", "Play")            # Play/Stop/Pause/Skip Fwd/Skip Rev
amp.set_shuffle("SERVER", True)
amp.cursor("SERVER", "Down")              # navigation menu DLNA/Net Radio
amp.tune_fm(99.1); amp.select_preset(3)   # tuner FM/AM
amp.remote_key("shuffle")                  # injection IR (device 7F01)

# escape hatch
amp.get("Main_Zone/Power_Control/Power")
amp.put("Main_Zone/Input/Input_Sel", "CD")
```

Référence complète : [`docs/YNC_COMMANDS.md`](docs/YNC_COMMANDS.md).

## Serveur web `webapp/`

Page de contrôle simple, responsive, sans dépendance externe + API JSON.
Thème auto (sombre/clair). Refresh auto 4 s, volume/mute poussés par GENA
via SSE (`/api/events`).

Fonctions : power, volume, mute, 11 sources, now-playing (pochette, play/pause/
skip, shuffle/repeat), tuner, navigateur DLNA/Net-Radio cliquable, push audio,
Network Standby, touches IR.

## Push DLNA

Le R-N301 expose un MediaRenderer UPnP sur `:8080`. Le projet sert le fichier en
HTTP (Range/206) puis envoie `SetAVTransportURI` + `Play` en SOAP. L'ampli
devient une **source audio** : TTS, webradios arbitraires, fichiers poussés
depuis un téléphone — tout devient possible.

```python
from rn301 import PushServer
push = PushServer("192.168.1.91")
push.push_file("track.flac", title="Morceau", artist="Moi")
push.wait_stopped(timeout=600)    # bloque sur l'event GENA, zéro polling
push.close()
```

Formats audio supportés : WAV, MP3, FLAC, MP4/M4A (l'audio d'une vidéo MP4 est
bien lu), ALAC. Non supportés : WMA, OGG/Opus (le renderer affiche `PLAYING`
mais aucun son ne sort).

Contraintes firmware : [`docs/DLNA_PUSH.md`](docs/DLNA_PUSH.md).

## Télécommande virtuelle (à venir)

Le device IR `7F01` a été scanné intégralement (256/256 OBC acceptés par le
firmware). Prochaine étape : capture des codes de la télécommande physique
RAX31 (volume, power, Speaker A/B, Balance…) pour enrichir `remote_key()` avec
les fonctions non exposées en YNC.

## Reverse engineering

Le R-N301 n'expose **aucun descripteur YNC** (`desc.xml` → 404) : l'app
officielle code les commandes en dur par modèle. Méthode : décompilation APK
(androguard), sondage live, brute-force IR, validation croisée. Scripts
d'analyse documentés dans [`tools/README.md`](tools/README.md), journal complet
dans [`docs/RESEARCH.md`](docs/RESEARCH.md).

## Arborescence

```
├── rn301/               # librairie
│   ├── rn301.py         #   client YNC (~90 ops, retries, verrou)
│   ├── push.py          #   push DLNA (FileServer + SOAP + events GENA)
│   ├── gena.py          #   abonné GENA
│   ├── discover.py      #   découverte SSDP
│   └── _locks.py        #   sérialisation par ampli
├── webapp/              # serveur web + page de contrôle + API JSON
├── tests/               # tests hors-ligne (unittest + faux renderer UPnP)
├── docs/                # documentation avancée
├── tools/               # scripts d'analyse → voir tools/README.md
└── amp/                 # réponses live archivées (probes, scans IR, SCPDs)
```

## Avertissements

- Projet amateur non affilié à Yamaha. YNC non documenté officiellement.
- Firmware capricieux : toujours prévoir des retries (la lib gère ça).
- `System/Misc/Update` peut déclencher une mise à jour firmware — à n'utiliser
  qu'en connaissance de cause.
- NET RADIO : le service vTuner intégré au R-N301 nécessite un compte payant
  (stations réduites) — c'est côté Yamaha/vTuner, pas côté protocole.

## Licence

MIT (voir `LICENSE`). L'APK et les PDF Yamaha restent propriété Yamaha — non
redistribués ici.
