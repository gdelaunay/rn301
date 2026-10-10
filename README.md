# 🎛️ rn301-py — Yamaha R-N301 : reverse engineering, librairie Python & contrôle web

> Le **repo** s'appelle `rn301-py` ; le **package Python** qu'il contient
> s'appelle `rn301` (`pip install rn301`, `from rn301 import …`).

Projet de contrôle complet d'un ampli hi-fi **Yamaha R-N301** (2013) sans cloud,
sans app officielle : protocole **YNC** (Yamaha Network Control) reverse-engineeré
à partir de l'APK "NP Controller" + sondage live de l'ampli.

**100 % Python stdlib** — aucune dépendance.

> **Pourquoi ce repo existe.** Le R-N301 ne sert **aucun descripteur YNC**
> (`/YamahaRemoteControl/desc.xml` → 404, `UnitDesc.xml` vide) : les librairies
> YNC génériques, qui découvrent les commandes via ce descripteur, ne peuvent
> donc **pas** fonctionner avec lui. (Il expose en revanche un `desc.xml` UPnP
> classique sur `:8080` — `amp/upnp_desc.xml`.) Ce repo est une implémentation complète et sans
> dépendance obtenue par reverse engineering de l'app officielle *NP Controller
> 4.60* + validation live commande par commande, et il couvre **les trois
> canaux de contrôle** de l'appareil : YNC (`:80`), MediaRenderer UPnP
> (`:8080`) et injection IR `Remote_Signal`.

```
                     ┌────────────────┐
                     │     rn301/     │
                     │   (library)    │
                     └───┬────────┬───┘
        HTTP + XML (YNC) │        │ UPnP/SOAP + GENA + HTTP
                     :80 │        │ :8080 (+ file server)
                 ┌───────▼────────▼──────┐
                 │      Yamaha R-N301     │
                 │      192.168.1.XX      │
                 │    (SSDP discovered)   │
                 └────────────────────────┘
```

## 🎯 État de l'art — ce que ce projet apporte

Le protocole YNC est déjà bien couvert pour les **amplis AV RX-V** (`rxv`,
`yamaha-av`, l'intégration Home Assistant `yamaha_ynca`…). Le R-N301, lui,
restait un angle mort : récepteur stéréo non-MusicCast, sans API officielle
YXC, sans `desc.xml`. Trois projets le ciblent, tous limités au YNC :

| Projet | Dernière activité | YNC | Push DLNA (`:8080`) | IR `Remote_Signal` |
|---|---|---|---|---|
| [`vrih/homeassistant-rn301`](https://github.com/vrih/homeassistant-rn301) | 2019 (abandonné) | ~10 ops | ❌ | ❌ |
| [`rihokirss/homeasisstant-rn301`](https://github.com/rihokirss/homeasisstant-rn301) (HACS) | actif | SERVER / NET RADIO / TUNER | ❌ | ❌ |
| [`STYL15HH1/Yamaha-Receiver-Controller`](https://github.com/STYL15HH1/Yamaha-Receiver-Controller) (Android, MIT) | actif, validé sur matériel | navigation + tuner avancés | ❌ | ❌ |
| **ce repo** | — | **~90 ops** extraites de l'APK, validées en live | ✅ **serveur de fichiers + AVTransport SOAP** | ✅ **device 7F01 scanné intégralement (256 OBC)** |

<sub>À la décharge des projets ci-dessus : `STYL15HH1` connaît le
MediaRenderer `:8080` mais ne l'utilise que pour la **découverte SSDP**
(identification modèle/UDN), jamais pour pousser de l'audio — aucun ne fait de
`SetAVTransportURI`. Même constat côté [`FHEM/71_YAMAHA_NP.pm`](https://github.com/mhop/fhem-mirror/blob/master/fhem/FHEM/71_YAMAHA_NP.pm),
qui ne lit `/MediaRenderer/desc.xml` que pour les icônes. C'est ce canal-là
(« l'ampli comme source DLNA de *notre* audio ») qui reste inédit.</sub>

La valeur ajoutée tient donc en trois points :

1. **Le push** — les autres projets *observent* la lecture ; celui-ci *fournit*
   l'audio à l'ampli (serveur HTTP local + `SetAVTransportURI`). C'est la
   différence entre une télécommande et une source : TTS/annonces, webradios
   arbitraires, fichiers poussés depuis un téléphone, tout devient possible.
2. **La télécommande virtuelle** — les 256 codes du device `7F01` ont été
   scannés un par un (256/256 acceptés par le firmware), les devices `7A01`
   (256 codes) et `7A85` (codes du service manual) testés et **écartés**,
   résultats archivés dans `amp/`. Le scan détecte les effets par *diff d'état
   YNC*, ce qui identifie les touches réseau (`0x3F` = cycle des sources) mais
   reste aveugle aux fonctions non exposées en YNC (volume, Speaker A/B,
   balance) — d'où la liste « reste à capturer » ci-dessous.
3. **Les deux protocoles ensemble** — YNC pour l'état/la navigation/le tuner,
   MediaRenderer pour l'audio, avec le savoir de *quand utiliser lequel*.

Voir aussi : [`docs/RESEARCH.md`](docs/RESEARCH.md) (journal de reverse, avec
provenance et horodatage des validations live),
[`docs/YNC_COMMANDS.md`](docs/YNC_COMMANDS.md), [`docs/DLNA_PUSH.md`](docs/DLNA_PUSH.md).

## 🚀 Démarrage rapide

```bash
# dans ce repo (aucune installation nécessaire, zéro dépendance)
python -c "from rn301 import discover_one, RN301; amp=RN301(discover_one()['host']); amp.power_on()"

# serveur web (page de contrôle sur http://localhost:8080)
python -m webapp.server --port 8080 --amp 192.168.1.91

# tests hors-ligne (aucun ampli requis)
python -m unittest discover tests
```

### 📦 Installer la lib dans un autre projet

```bash
# uv — path dependency (editable : les modifs de la lib sont visibles direct)
uv add ../rn301-py --editable

# pip classique
pip install -e ../rn301-py

# une fois le repo pushé sur un forge :
uv add git+https://github.com/<user>/rn301-py
```

Le package installe aussi deux CLI : `rn301-discover` (scan SSDP → identité
complète de l'ampli en JSON) et `rn301-gena` (écoute live des events GENA).
Type hints inclus (`py.typed`).

## 📦 La librairie `rn301/`

Client YNC complet, **validé commande par commande en live** sur le firmware
1.13/0.05. Gère les quirks du R-N301 : requête unique par POST, retry sur
réponses vides/400, retry long (5 s) sur RC=3 ("occupé" après changement de
source réseau), GET de blocs complets plutôt que de sous-éléments.

```python
from rn301 import RN301, discover_one
amp = RN301(discover_one()["host"])   # découverte SSDP — ou IP fixe

# --- zone principale
amp.power_on(); amp.power_off(); amp.toggle_power()
amp.set_volume(25)          # échelle protocole 0..100 ; l'ampli clampe à son
                            # réglage « Max Volume » (80 par défaut en façade).
                            # RN301(host, max_volume=45) = plafond logiciel
amp.volume_up(2); amp.volume_down(2)
amp.set_mute(True); amp.toggle_mute()
amp.set_input("SERVER")     # 11 entrées: Spotify, NET RADIO, SERVER, AirPlay,
                            # TUNER, CD, OPTICAL, COAXIAL, LINE1..3
amp.list_inputs()           # entrées + icônes
amp.basic_status()          # power/volume/mute/input d'un coup

# --- lecture réseau (SERVER, NET_RADIO, Spotify, AirPlay)
amp.playback("SERVER", "Play")     # Play/Stop/Pause/Skip Fwd/Skip Rev
amp.set_shuffle("SERVER", True)
amp.set_repeat("SERVER", "All")    # Off/One/All
amp.play_info("current")           # artiste/album/pochette...

# --- navigation listes DLNA / Net Radio (8 lignes + curseur)
amp.list_info("SERVER")
amp.cursor("SERVER", "Down")       # Up/Down/Left/Right/Sel/Return
amp.select_line("SERVER", 4)       # clic direct Line_1..8
amp.page("SERVER", "Down")

# --- tuner FM/AM
amp.set_band("FM")
amp.tune_fm(99.1); amp.tune_am(1197)
amp.tuning_step("Up")              # pas de 50 kHz FM / 9 kHz AM (calculé côté client)
amp.select_preset(3)               # 1..40, ou "Up"/"Down"
amp.get_freq()                     # ('FM', 99.1)

# --- injection IR (touches télécommande virtuelles)
amp.remote_key("shuffle")          # 7F0146B9
amp.remote_key("repeat")           # 7F0145BA
amp.remote_key("net_cycle")        # 7F013FC0 : SERVER→NET RADIO→AirPlay→Spotify
amp.remote_signal("7F0146B9")      # code brut (device 7F01 + OBC + ~OBC)

# --- système / réseau
amp.system_config()                # modèle, version, features
amp.get_network_standby(); amp.set_network_standby(True)
amp.get_network_name(); amp.set_network_name("Salon")
amp.get_mac_filter(); amp.set_mac_filter("Off")

# --- escape hatch
amp.get("Main_Zone/Power_Control/Power")
amp.put("Main_Zone/Input/Input_Sel", "CD")
amp._request("GET", "<System><Config>GetParam</Config></System>")
```

## 🌐 Le serveur web `webapp/`

Page de contrôle sombre et responsive (aucun asset externe) + API JSON.
Refresh auto toutes les 4 s.

**Contrôles de la page** : power, volume (slider 0–80 + −/+), mute, 11 sources,
lecteur "now playing" (pochette, play/pause/skip, shuffle/repeat), tuner
(bande, fréquence, pas-à-pas, presets 1–8), **navigateur DLNA/Net Radio
cliquable** (le menu de l'ampli affiché dans le navigateur !), **push audio
DLNA** (upload d'un fichier ou URL → joué sur l'ampli), Network Standby,
touches IR.

**API** (`POST` JSON, renvoie `{ok, state}` complet) :

| Route | Body | Action |
|---|---|---|
| `/api/state` (GET) | — | état agrégé (zone + play_info + list_info + réseau) |
| `/api/power` | `{on:bool}` ou `{}` | set / toggle |
| `/api/volume` | `{val:0..80}` ou `{delta:±n}` | absolu / relatif |
| `/api/mute` | `{on:bool}` ou `{}` | set / toggle |
| `/api/input` | `{val:"SERVER"}` | changement de source |
| `/api/playback` | `{action:"Play", source?}` | Play/Stop/Pause/Skip Fwd/Skip Rev |
| `/api/playmode` | `{what:"shuffle"\|"repeat", value}` | modes de lecture |
| `/api/list` | `{source?}` | menu de navigation brut |
| `/api/cursor` | `{dir:"Down"}` | Up/Down/Left/Right/Sel/Return |
| `/api/page` | `{dir:"Down"}` | pagination |
| `/api/line` | `{n:4}` | sélection directe Line_1..8 |
| `/api/tuner` | `{op:"tune"\|"band"\|"step"\|"preset"\|"preset_step", ...}` | tout le tuner |
| `/api/netstandby` | `{on:bool}` ou `{}` | set / toggle |
| `/api/netname` | `{name}` | nom réseau |
| `/api/macfilter` | `{mode, a1..a6}` | filtre MAC |
| `/api/remote` | `{key:"shuffle"}` ou `{code:"7F01..."}` | IR virtuel |
| `/api/raw` | `{cmd:"GET", xml:"<...>GetParam</...>"}` | YNC brut (exploration) |
| `/art?url=` (GET) | — | proxy pochettes d'album |
| `/api/upload?name=` (POST) | body binaire brut | upload audio depuis le navigateur → `music/` |
| `/api/push` | `{file\|url, title?, artist?, album?}` | **push DLNA** : pousse l'audio au renderer et joue |
| `/api/pushctl` | `{action:"Play\|Pause\|Stop"}` | transport du renderer |

## 📲 Push DLNA — envoyer n'importe quel audio à l'ampli

Le R-N301 expose aussi un **MediaRenderer UPnP** sur `:8080`
(`/MediaRenderer/desc.xml`, services AVTransport/RenderingControl/
ConnectionManager). Comme l'app officielle avec la musique du téléphone
(module `localfilecontroller`), on peut lui pousser directement une URL :
le projet sert le fichier en HTTP (`FileServer`, Range/206) puis envoie
`SetAVTransportURI`+`Play` en SOAP. **Validé en live : WAV, MP3, FLAC, MP4/M4A** ✅
(pause/reprise OK ; seek refusé par le firmware).

```python
from rn301 import PushServer
push = PushServer("192.168.1.91")
push.push_file("morceau.flac", title="Mon morceau", artist="Moi")
push.pause(); push.play(); print(push.state()); push.stop()
push.push_url("http://nas/radio.mp3")          # webradio/NAS direct

# --- événements de fin de piste (GENA) : la lib notifie, le client décide
push.on_transport(lambda state, changed: print(state))   # 'PLAYING', 'STOPPED'…
push.push_file("piste.flac")
push.wait_stopped(timeout=600)     # bloque sur l'event, zéro polling
push.close()                       # UNSUBSCRIBE + arrêt du serveur de fichiers
```

Dans la webapp : carte **"📲 Push audio"** (upload d'un fichier depuis le
navigateur — y compris depuis un téléphone — ou URL directe, boutons
transport). Options : `--push-port` (9000), `--music` (dossier d'upload),
`--no-push`. Détails et coulisses du reverse : **`docs/DLNA_PUSH.md`**.

Le renderer honore aussi l'**eventing GENA** (`SUBSCRIBE …/evt` → `SID` +
`NOTIFY` à chaque changement), implémenté dans la lib : `GenaClient`
(`rn301/gena.py`) et les événements `PushServer` (`on_transport`,
`wait_stopped`). Périmètre mesuré en live : GENA notifie le **volume/mute**
(façade et télécommande comprises, ~30 ms plus vite que le polling) et le
**transport du push DLNA** — jamais `Power`, `Input_Sel` ni la lecture réseau :
il complète le polling YNC, ne le remplace pas. La webapp l'exploite :
volume/mute arrivent en push via SSE (`/api/events`), le reste de l'état reste
pollé toutes les 4 s. Contrat, comportements mesurés
et pièges du protocole : **`docs/DLNA_PUSH.md` §5**.

```python
from rn301 import GenaClient
gena = GenaClient("192.168.1.91")
gena.subscribe("RenderingControl", lambda svc, changed: print(changed))
# bouton de façade tourné → {'Volume': {'channel': 'Master', 'val': '52'}}
gena.var("RenderingControl", "Volume")   # état courant, zéro requête
gena.close()
```

## 🔬 Le reverse engineering (docs/)

Le R-N301 n'expose **aucun descripteur YNC** (`/YamahaRemoteControl/desc.xml`
→ 404, `UnitDesc.xml` vide) : l'app officielle code les commandes en dur par
modèle. (Le `desc.xml` **UPnP** du renderer `:8080`, lui, existe bien et a été
archivé dans `amp/upnp_desc.xml` — il contient même un bloc vendor
`<yamaha:X_device>` qui pointe vers l'endpoint YNC, d'où un chemin de
découverte complet sans IP fixe : SSDP → `:8080/MediaRenderer/desc.xml` →
`X_URLBase` + `X_controlURL`.) Méthode employée :

1. **Décompilation de l'APK** NP Controller v4.60 (androguard) →
   `q.java` = builder YNC du R-N301, ~90 opérations (`docs/ops_brut.txt`)
2. **Sondage live** commande par commande (`tools/probe_gets.py` →
   `amp/probe_gets.json`)
3. **Brute-force IR** : `Remote_Signal` accepte des codes NEC étendus
   `7F01 + OBC + ~OBC` — scan des 256 OBC (`tools/scan_remote.py`)
4. **Validation croisée** : chaque chemin de l'app testé en live, les refus
   (400/RC=3) documentés

### Découvertes clés

- Tag source réseau = **`NET_RADIO`** (pas `Net_Radio` comme ailleurs dans la doc communautaire)
- `Input_Sel` exige **`TUNER` en majuscules** (`Tuner` → RC=3)
- **Volume : échelle protocole 0..100, identique** sur les **deux** canaux (YNC
  et UPnP) — mesurée en live au plafond (80 = 80). Le plafond effectif vient du
  réglage « Max Volume » de l'ampli (modifiable sur la façade, aucun chemin YNC
  de lecture) : un `PUT` de 100 est clampé par le firmware, et le
  `allowedValueRange 0..100` du SCPD UPnP est purement **déclaratif**. La lib
  travaille donc sur 0..100 et laisse l'ampli clampler (+ `max_volume=`
  optionnel, plafond logiciel). Volume relatif `Up x.x dB` refusé →
  lire/calculer/écrire
- **RC=3 = occupé** : ~3-5 s de digestion après un changement de source réseau,
  retry rapprochés contre-productifs → délai 5 s
- Tuner : chemin `Tuning/Freq/{FM,AM}`, pas d'auto-search YNC, presets sans `<Val>`
- Réseau sous `System/Misc/Network/` (pas `System/Config/`)
- **MediaRenderer UPnP sur `:8080`** (`/MediaRenderer/desc.xml`) : push audio
  direct via `SetAVTransportURI` — WAV/MP3/FLAC validés en live, seek refusé,
  `SetNextAVTransportURI` accepté mais sans effet (stub) → `docs/DLNA_PUSH.md`
- ❌ Non supportés en YNC : Sleep, Gapless, Device_Name, FM_Mode, Jump_Line,
  Track_Number, Balance, Tone, Speaker A/B → façade ou télécommande IR physique uniquement

### Remote_Signal — IR virtuel

```xml
<YAMAHA_AV cmd="PUT"><System><Misc><Remote_Signal><Receive>
  <Code>7F0146B9</Code>
</Receive></Remote_Signal></Misc></System></YAMAHA_AV>
```

Seul le device **7F01** produit des effets. Codes connus : `0x46` Shuffle,
`0x45` Repeat (utilisés par l'app officielle), `0x3A`/`0x3F` sources réseau.
Le firmware accepte **n'importe quel** code `7F01+OBC+~OBC` sans le valider
(256/256 au scan) : `Remote_Signal` est un simple *blaster* IR, la détection
d'un effet ne peut donc passer que par l'observation de l'état.
**Reste à capturer** : les codes de la télécommande RAX31 (volume, power,
Speaker A/B, Balance...) via récepteur IR — si leur device ≠ 7F01, il faudra un
émetteur IR physique (ESP32 + LED) pointé sur la façade.
Le code est un **NEC1 étendu** : `7F` = device 127, `01` = **vrai sub-device**
(127 + 1 ≠ 255, ce n'est donc pas un complément), `B9` = OBC, `46` = ~OBC.
C'est ce qui explique l'échec des scans sur `7A01`/`7A85` : mauvais device.
Piste pour **nommer** les 256 codes déjà scannés sans nouvelle capture : la doc
Yamaha « Remote Control Code » liste les codes au format `device-OBC` (ex.
`7A-5C23`) — reste à vérifier si une section `7F-` y couvre le R-N301, et si la
télécommande physique RAX31 émet bien sur 7F.01.

## 📁 Arborescence

```
├── rn301/               # ⭐ la librairie
│   ├── rn301.py         #   client YNC (~90 ops validées, retries, verrou)
│   ├── push.py          #   push DLNA (FileServer + SOAP AVTransport + events)
│   ├── gena.py          #   abonné GENA (GenaClient, wait_for)
│   ├── discover.py      #   découverte SSDP sans IP fixe
│   └── _locks.py        #   sérialisation par ampli (1 requête à la fois)
├── webapp/              # ⭐ serveur web + page de contrôle + API JSON
├── tests/               # tests hors-ligne (unittest + faux renderer UPnP)
├── docs/
│   ├── YNC_COMMANDS.md  # référence des commandes YNC validées en live
│   ├── DLNA_PUSH.md     # référence MediaRenderer :8080 (push + GENA)
│   ├── RESEARCH.md      # journal de reverse engineering (provenance, mesures)
│   └── ops_brut.txt     # table des ~90 ops YNC extraites de l'APK
├── tools/               # scripts d'analyse (androguard, sondage, scan IR,
│                        #   sondes UPnP read-only : probe_gena*.py)
└── amp/                 # réponses live archivées (probe_gets.json, scans IR,
                         #   upnp_desc.xml + les 3 SCPD : avtransport,
                         #   rendering, connectionmanager)
```

Fichiers **non distribués** (propriété Yamaha/tiers, absents du repo) : le
manuel `R-N301_om_G.pdf`, la doc `Remote_Control_Code.pdf`, l'APK NP Controller
— à retélécharger/recharger soi-même si besoin (procédure ci-dessous pour
l'APK).

Pour reproduire l'analyse de l'app officielle : téléchargez l'APK
**NP Controller v4.60** sur
[APKPure](https://apkpure.com/network-player-controller/com.yamaha.npcontroller)
et placez-le à la racine du projet (nom attendu par les scripts :
`NETWORK PLAYER CONTROLLER_4.60_APKPure.apk`). Décompression/décompilation :

```bash
pip install androguard loguru   # dans le .venv

# 1. strings + inventaire des classes        -> apk/decompiled/
python tools/analyze_apk.py strings
python tools/analyze_apk.py classes

# 2. trouver les classes qui construisent le XML YNC, puis les décompiler
python tools/decompile.py refs "Main_Zone"
python tools/decompile.py src com/yamaha/npcontroller/j/q   # -> apk/decompiled/src/…/q.java

# 3. extraire la table des ~90 opérations YNC depuis q.java
python tools/extract_ops.py > docs/ops_brut.txt

# 4. valider en live commande par commande contre l'ampli
python tools/probe_gets.py      # -> amp/probe_gets.json
```

## ⚠️ Avertissements

- Projet amateur non affilié à Yamaha. YNC est non documenté officiellement.
- Le firmware R-N301 est capricieux : toujours prévoir des retries (la lib le fait).
- `System/Misc/Update` peut déclencher une mise à jour firmware : à n'utiliser
  qu'en connaissance de cause.
- NET RADIO : le service vTuner intégré au R-N301 est en fin de vie (compte
  payant / stations réduites) — c'est côté Yamaha/vTuner, pas côté protocole.

## 📄 Licence

Code du projet : **MIT** (voir `LICENSE`). L'APK et les PDF Yamaha restent
la propriété de Yamaha — ne pas redistribuer.
