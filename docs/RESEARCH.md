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
- **Format du code découvert** : `7F01` (préfixe Yamaha) + `XX` (touche) + `~XX` (complément).
  Vérif : `0x46 XOR 0xB9 = 0xFF`, `0x45 XOR 0xBA = 0xFF`.
  → **Seulement 256 codes possibles**, brute-force trivial.
  Codes connus : `0x46` = Shuffle, `0x45` = Repeat.
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

## Arborescence du projet
```
├── NETWORK PLAYER CONTROLLER_4.60_APKPure.apk
├── R-N301_om_G.pdf          # manuel
├── manual.txt               # manuel converti en texte
├── apk/
│   ├── extracted/           # APK dézippé
│   └── decompiled/          # strings_all.txt, strings_ync.txt, classes.txt, src/
├── amp/                     # réponses live de l'ampli (system_config.xml, input_items.xml)
├── docs/RESEARCH.md         # ce fichier
└── tools/                   # scripts d'analyse (androguard)
```
