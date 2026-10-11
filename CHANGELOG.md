# Changelog

`rn301` — format [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
versioning [SemVer](https://semver.org/lang/fr/).

---

## 1.0.0 — version initiale

Première version **publique** et **stable** : la librairie, la webapp et la
documentation sont complètes et validées en live sur un R-N301 (firmware
1.13/0.05).

### Ajouté

- **`rn301`** — client YNC 100 % stdlib : power, volume (clamp logiciel
  optionnel), mute, sélection d'entrée, lecture, tuner FM/AM (presets,
  auto-search, pas d'accord), shuffle/repeat, navigation dans les menus DLNA /
  Net Radio (cursor, page, line), réglages réseau (`Network_Standby`,
  `Network_Name`), filtrage MAC, `Remote_Signal` (IR virtuel).
- **`rn301.discover`** — découverte SSDP (M-SEARCH → `desc.xml` → filtre
  Yamaha R-N301), extraction IP + endpoint YNC + icônes + `presentationURL`.
  CLI : `rn301-discover`. Multi-interfaces (fix multi-homing).
- **`rn301.gena`** — client GENA stdlib : serveur de callback, renouvellement
  SID, LastChange **incrémental** accumulé par deltas, trous SEQ comptés,
  `wait_for(predicate, timeout)`.
- **`rn301.push`** — serveur HTTP mono-fichier (Range/206/HEAD, extraction
  tags + pochette ID3v2/FLAC/MP4) et client SOAP AVTransport : push d'un
  fichier local ou d'une URL (`push_url`) vers le MediaRenderer `:8080` de
  l'ampli. `wait_stopped` en deux phases sur event GENA (anti-course « vieux
  STOPPED »), sans polling.
- **`webapp`** — serveur de contrôle web (stdlib uniquement) : API JSON,
  découverte SSDP en tâche de fond au démarrage, re-bind à chaud
  (`POST /api/rediscover`), bind manuel, SSE `/api/events` pour les
  notifications GENA volume/mute, UI responsive (navigateur DLNA cliquable,
  placeholder pochette, amorçage ~200 ms, sections repliables, vue Aide).
  **i18n FR/EN** (`webapp/static/i18n.json`, `data-i18n`/`-ph`/`-title`/`-aria`,
  détection `navigator.language` + override persistant).
- **Formats audio poussables — validés en live** : WAV/LPCM 44.1/48k stéréo,
  MP3, FLAC (y c. 96k/192k hi-res), MP4/M4A (AAC), ALAC (.m4a).
  Non supportés par le firmware (limitation matérielle, pas un bug de la
  lib) : WMA, OGG/Vorbis, Opus, AC3.
- **Documentation** — `README.md` (FR, version de référence) + `README.en.md`
  (EN), `docs/DLNA_PUSH.md`, `docs/RESEARCH.md`, `docs/YNC_COMMANDS.md`,
  `docs/ops_brut.txt`, `tools/README.md`, `CONTRIBUTING.md`.
- **Tests hors-ligne** — 27 tests Python (`unittest`, mock sockets) +
  37 tests UI webapp en `.mjs` sans dépendance npm (harness maison,
  `node webapp/tests/*.mjs`).
- **Outils de reverse engineering** dans `tools/` : `analyze_apk.py`,
  `decompile.py`, `extract_ops.py`, `probe_gena*.py`, `probe_gets.py`,
  `scan_remote.py`, `test_push.py` (documentés dans `tools/README.md`).
- **Captures matérielles** dans `amp/` : SCPD AVTransport/ConnectionManager/
  RenderingControl, `desc.xml`, `system_config.xml`, `input_items.xml`,
  scans IR et probes GET (JSON structurés).
- **Règles projet** dans `AGENTS.md` : stdlib-only + sérialisation YNC,
  traduction obligatoire à chaque ajout de chaîne visible.

### Licences

MIT. Les APK/PDF Yamaha restent propriété Yamaha ; aucune redistribution.
