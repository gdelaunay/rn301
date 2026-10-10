# Recherche — Yamaha R-N301 / Network Player Controller

## Le matos
- **Ampli** : Yamaha R-N301, firmware `1.13/0.05`, System_ID `0ED9C263`
- **IP** : `192.168.1.91`
- **Serveur HTTP embarqué** : `Network_Player/3.1 (R-N301)`
- **App officielle** : NP Controller v4.60 (package `com.yamaha.npcontroller`, target SDK 26,
  fonctionne en sideload sur Android moderne)

## Protocole : YNC (Yamaha Network Control)
- Non documenté officiellement par Yamaha, mais bien connu de la communauté
  (yamaha-nodejs, intégrations Home Assistant, etc.)
- **Endpoint** : `POST http://192.168.1.91/YamahaRemoteControl/ctrl`
- **Format** : XML `<YAMAHA_AV cmd="GET|PUT">...</YAMAHA_AV>`, valeurs lues via `GetParam`
- **Comportement** : si un seul élément de la requête est invalide → HTTP 400, corps vide.
  Il faut donc sonder les commandes **une par une**.

### Ce qui n'existe PAS sur le R-N301
- `GET /YamahaRemoteControl/desc.xml` → 404
- `GET /YamahaRemoteControl/UnitDesc.xml` → 404
- `GETDESC` → HTTP 400

Le R-N301 n'expose **aucun fichier de description** : l'app officielle code en dur les
commandes par modèle (une Activity par famille : `MainRN500` pour R-N500/R-N301,
`MainCDN301`, `MainCRX560`...).

## Inventaire des commandes (sondées en live + confirmées par décompilation)

### ✅ Fonctionnelles (vérifiées sur l'ampli)
| Commande | Chemin YNC |
|---|---|
| Power (zone) | `<Main_Zone><Power_Control><Power>` (On/Standby) |
| Power (système) | `<System><Power_Control><Power>` |
| Volume | `<Main_Zone><Volume><Lvl><Val>` (0..~100 ?) |
| Mute | `<Main_Zone><Volume><Mute>` (On/Off) |
| Input courant | `<Main_Zone><Input><Input_Sel>` |
| Liste des inputs | `<Main_Zone><Input><Input_Sel_Item>` |
| Config système | `<System><Config>` (model, version, features) |

**Inputs dispo** : Spotify, NET RADIO, SERVER, AirPlay, TUNER, CD, OPTICAL, COAXIAL, LINE1-3

### ✅ Présentes dans le code de l'app (pas encore testées en live)
`Tuner` (Band/Freq/Preset/Tuning), `List_Control` (navigation NET_RADIO/SERVER/Spotify :
Cursor, Line, Jump), `Net_Radio Play_Info` (Station_Name, Song, Album...), `SERVER Play_Info`,
`Network Setup` (IP/DNS/MAC filter), `Sleep` (?), `Remote_Signal` (?!), `Network_Standby`.

> 💡 `Remote_Signal` : CONFIRMÉ. L'app l'utilise pour les touches Shuffle/Repeat
> (non pilotables en YNC classique). Voir section dédiée plus bas.

### ❌ Confirmé inexistantes via YNC (sondage live → HTTP 400 + absentes du code de l'app)
- Balance (`Sound_Video/Balance`, `Balance`, variantes) 
- Tone control (`Tone`, `Bass`, `Treble`)
- Speaker A/B (`Speaker`, `Spkr_Sel`, `Speaker_A/B`, `Spkr`)
- Surround/DSP (`Surround`, `Program_Sel`)

Ces fonctions sont **uniquement accessibles en façade ou via la télécommande IR**
(manuel p.34 : Setup menu → Tone Control / Balance ; boutons SPEAKERS A/B).
Pas d'entrée IR (REMOTE IN) au dos non plus — juste le capteur IR en façade.

## Reverse engineering de l'APK
- Outils : Python + [androguard 4.1.4] (dans `.venv/`), scripts dans `tools/`
- `tools/analyze_apk.py strings|classes` → dumps dans `apk/decompiled/`
- `tools/decompile.py refs <string>` → trouve les classes qui référencent un string
- `tools/decompile.py src <classe>` → décompile une classe en pseudo-Java (`apk/decompiled/src/`)
- La classe **`com/yamaha/npcontroller/j/q.java`** (1079 lignes) = le builder de
  toutes les commandes YNC pour les modèles R-N. Inventaire des tags = preuve
  que l'app ne gère ni Balance ni Speaker A/B.
- L'app parle aussi UPnP/DLNA (`:49154/MediaRenderer/desc.xml`) pour le streaming
  local (service AIDL `com.yamaha.av.localfilecontroller` = lecture des fichiers du téléphone).

## Remote_Signal — injection de touches télécommande via YNC ✅ CONFIRMÉ

```xml
<YAMAHA_AV cmd="PUT"><System><Misc><Remote_Signal><Receive><Code>7F0146B9</Code>
</Receive></Remote_Signal></Misc></System></YAMAHA_AV>
```

- Testé en live : **RC="0"** (accepté), contrairement aux commandes inconnues → HTTP 400.
- Usage dans l'app (`com/yamaha/npcontroller/b/t.java` + `d/ao.java`) :
  - `7F0146B9` = touche **SHUFFLE** (`q.e("On"/"Off")`, op 4100)
  - `7F0145BA` = touche **REPEAT** (`q.d("Single"/"All"/"Off")`, op 4099)
- **Format du code découvert** : `7F01` + `XX` (touche) + `~XX` (complément).
  Vérif : `0x46 XOR 0xB9 = 0xFF`, `0x45 XOR 0xBA = 0xFF`.
  → **Seulement 256 codes possibles**, brute-force trivial.
  Codes connus : `0x46` = Shuffle, `0x45` = Repeat.
  *(Interprétation corrigée plus tard : NEC1 étendu — `7F` device 127, `01`
  **vrai sub-device** (127+1 ≠ 255), `XX` = OBC, `~XX` = complément. C'est ce
  qui explique l'échec des scans sur `7A01`/`7A85` : mauvais device.)*
- `tools/scan_remote.py` : scanne les 256 codes en observant power/volume/input/mute via YNC.
  Les effets non observables (Balance, Speaker A/B, menus) demandent une vérif visuelle/auditive.

## Résultats de scan & recodage (session 2)

### Scan complet device 7F01 (256 codes, delay 1.5s, retry GET)
- Seuls effets observables : `0x3A` et `0x3F` → changement d'input
- **`0x3F` = touche CYCLE des sources réseau** (SERVER→NET RADIO→AirPlay→Spotify→...),
  PAS une touche directe AirPlay
- Volume/Power/Mute : AUCUN effet → ces touches ne sont pas sur device 7F01,
  OU l'effet n'est pas observable YNC (Speaker A/B, Balance, Menu : invisibles !)

### Scan complet device 7A01 (256 codes) → zéro effet
### Tests device 7A85 (complément NEC1 de 7A) avec codes service manual → zéro effet
- Power OFF `7A-1E` → `7A851EE1` : pas d'effet
- Vol+ `7A-1A` → `7A851AEA` : pas d'effet
- Variantes d'ordre de bytes (857A…, E11E…, inversés) : rien
- **L'injection Remote_Signal ne décode apparemment QUE le device 7F01**
- Codes "service manual" de l'agent (device 7A, JP1-style) non confirmés tels quels

### Fiabilité
- L'injection marche toujours (re-validée avec 7F013FC0 en boucle)
- Parfois un échec isolé (file d'attente IR?) → prévoir retry côté client

## Options pour balance / Speaker A-B
1. **`Remote_Signal`** — ✅ piste validée, reste à trouver les codes des touches (scan des 256)
2. **IR physique** — un ESP32 + LED IR pointé vers le capteur de façade, en rejouant les
   codes de la télécommande (protocole NEC Yamaha). Il faut capturer les codes avec un
   récepteur IR (VS1838B) ou trouver une base de codes (LIRC / remotes centra).
3. Renoncer pour ces fonctions et faire un beau contrôleur avec le reste.

## Session 2025-10 — MediaRenderer UPnP :8080, push DLNA, eventing GENA

> Faits consolidés et API : `docs/DLNA_PUSH.md`. Ici : la provenance et les
> conditions de mesure.

### Découverte du renderer
- `GET :8080/MediaRenderer/desc.xml` → 200 (contre 404 pour tout descripteur
  YNC) : friendlyName, UDN, 3 services (AVTransport / RenderingControl /
  ConnectionManager), bloc vendor `<yamaha:X_device>` avec `X_URLBase` +
  `X_controlURL` → l'endpoint YNC est découvrable via SSDP.
- Décompilation du module `com.yamaha.av.localfilecontroller` de l'app (22
  classes) : téléphone = serveur HTTP (Range/206) + point de contrôle SOAP.
  Mapping `DLNA.ORG_OP` par format recopié de `p.java`.
- Implémentation `rn301/push.py` validée en live : WAV/LPCM ✅, MP3 ✅, FLAC ✅,
  pause/reprise avec `rel_time` conservé ✅. `Seek` → SOAP 500 « mode not
  supported » ; piége confirmé : déclarer OP=01 sur un WAV → `ERROR_OCCURRED`
  à la reprise. `SetNextAVTransportURI` accepté mais **stub** (aucun effet) →
  pas de gapless. SCPD AVTransport capturé (`amp/upnp_avtransport.xml`, 15
  actions, `NumberOfTracks 0..1`, `PlayMode NORMAL` seul).
- `TransportStatus=ERROR_OCCURRED` au repos expliqué : le renderer conserve la
  dernière URI poussée et tente de la recharger après l'arrêt du serveur de
  fichiers — erreur résiduelle, pas un état actif.

### Mesure GENA (tools/probe_gena.py + probe_gena_raw.py)
- Protocole validé : SUBSCRIBE → 200 + SID, `TIMEOUT: Second-600` demandé →
  **`Second-300` rendu** (plafond firmware), 1 NOTIFY initial immédiat (SEQ=0,
  état complet), UNSUBSCRIBE → 200. Stack `KnOS/3.2 UPnP/1.0 DMP/3.5`.
- Couplage YNC↔UPnP mesuré sur **420 s** avec manipulations physiques (bouton
  de façade + télécommande), polling YNC `Basic_Status` toutes les 0,5 s en
  parallèle : 27 NOTIFY RenderingControl (volume/mute, ~30 ms **avant** que le
  polling YNC voie le changement), 1 seul AVTransport (l'initial). Zéro event
  sur Power, Input_Sel, lecture réseau SERVER → **GENA complète le polling, ne
  le remplace pas**. Les deux piles sont étanches : Play/Pause AVTransport
  pendant une écoute SERVER = aucun effet.
- Pièges mesurés : `LastChange` incrémental (SEQ=0 complet, ensuite deltas),
  events coalescés (valeurs intermédiaires sautées, finale fiable), attributs
  `channel` avant `val` (regex naïve = events « vides »), `ERROR_OCCURRED`
  résiduel.
- Échelle de volume **tranchée** : au plafond, GENA `Volume[Master]`=80 et YNC
  `Basic_Status/Val`=80 au même instant → échelles identiques, le `0..100` du
  SCPD est déclaratif. Le réglage « Max Volume » (façade) n'a **aucun chemin
  YNC de lecture** (absent des ~90 ops de l'APK et de `probe_gets.json`).

## Session 2025-10 (bis) — Consolidation : la recherche devient la lib

Tout ce qui était « validé mais pas implémenté » est passé dans le code,
avec tests hors-ligne (`tests/`, 27 tests) et validation live :

- **`rn301/gena.py`** : abonné GENA de production (callback HTTP 200,
  renouvellement SID ~240 s + re-SUBSCRIBE sur 404/412, LastChange
  double-décodé et accumulé par deltas, trous de SEQ, `wait_for`). Validé
  live sur l'ampli (volume façade/télécommande reçus en push).
- **`rn301/discover.py`** : découverte SSDP (M-SEARCH MediaRenderer+rootdevice,
  filtre manufacturer/model, dédoublonnage UDN, LOCATION≠émetteur écartée) →
  IP, modèle, serial, endpoint YNC via `X_URLBase`, icônes, presentationURL.
  Validé live bout en bout (`discover_one()` → `RN301(host).basic_status()`).
  Parsing testé sur le desc.xml réel archivé.
- **`PushServer`** : `on_transport(callback)` + `wait_stopped()` deux phases
  (armement PLAYING puis STOPPED — anti-course vieux STOPPED) + `close()`.
  Paresseux : aucun abonnement GENA sans usage explicite. Pas de file
  d'attente dans la lib (décision de design : la lib notifie, le client décide).
- **Fixes** : pochette push `/albumart` en 404 (`cover_data` lu sur le mauvais
  objet) ; verrou partagé par ampli YNC+SOAP (`_locks.py`, une seule requête à
  la fois) ; volume = échelle protocole 0..100 + clamp firmware +
  `max_volume=` logiciel optionnel (le 80 codé en dur était faux si « Max
  Volume » façade ≠ 80).
- **MP4/M4A (AAC) validé en push** ✅ (test utilisateur). WMA : non testé, hors
  intérêt.
- Dernier contrat non archivé capturé : `amp/upnp_connectionmanager.xml`.
- Décision : **pas de client RenderingControl** (SetVolume UPnP) — le contrôle
  volume reste en YNC, GENA fournit déjà les events ; une seconde voie
  d'écriture serait du code mort.

## Arborescence du projet
```
├── rn301/                   # ⭐ la librairie
│   ├── rn301.py             # client YNC (power/volume/input/playback/listes/tuner/IR)
│   ├── push.py              # push DLNA (FileServer + Renderer SOAP + PushServer)
│   ├── gena.py              # abonné GENA (GenaClient)
│   ├── discover.py          # découverte SSDP
│   └── _locks.py            # verrou partagé par ampli
├── webapp/                  # serveur web de contrôle (page + API JSON)
├── tests/                   # tests hors-ligne (unittest, faux renderer UPnP)
├── docs/                    # RESEARCH.md (journal), YNC_COMMANDS.md, DLNA_PUSH.md
├── tools/                   # scripts de reverse et sondes live
├── amp/                     # réponses live archivées (JSON, desc.xml, SCPD)
└── apk/                     # décompilation APK (gitignoré)
```
