# 🎛️ rn301 — Yamaha R-N301 : reverse engineering, librairie Python & contrôle web

Projet de contrôle complet d'un ampli hi-fi **Yamaha R-N301** (2013) sans cloud,
sans app officielle : protocole **YNC** (Yamaha Network Control) reverse-engineeré
à partir de l'APK "NP Controller" + sondage live de l'ampli.

**100 % Python stdlib** — aucune dépendance.

```
┌──────────────┐   HTTP/XML (YNC)   ┌──────────────┐   DLNA   ┌────────┐
│  Navigateur  │ ─────────────────► │  webapp/     │ ───────► │  NAS   │
│  (page ctrl) │                    │  serveur.py  │          └────────┘
└──────────────┘                    └──────┬───────┘
                                           │
                                    ┌──────▼───────┐
                                    │ Yamaha R-N301│  192.168.1.91
                                    └──────────────┘
```

## 🚀 Démarrage rapide

```bash
# librairie
python -c "from rn301 import RN301; amp=RN301('192.168.1.91'); amp.power_on(); amp.set_volume(25)"

# serveur web (page de contrôle sur http://localhost:8080)
python -m webapp.server --port 8080 --amp 192.168.1.91
```

## 📦 La librairie `rn301/`

Client YNC complet, **validé commande par commande en live** sur le firmware
1.13/0.05. Gère les quirks du R-N301 : requête unique par POST, retry sur
réponses vides/400, retry long (5 s) sur RC=3 ("occupé" après changement de
source réseau), GET de blocs complets plutôt que de sous-éléments.

```python
from rn301 import RN301
amp = RN301("192.168.1.91")

# --- zone principale
amp.power_on(); amp.power_off(); amp.toggle_power()
amp.set_volume(25)          # 0..80 (validé en live)
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
cliquable** (le menu de l'ampli affiché dans le navigateur !), Network Standby,
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

## 🔬 Le reverse engineering (docs/)

Le R-N301 n'expose **aucun desc.xml** : l'app officielle code les commandes en
dur par modèle. Méthode employée :

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
- **Volume max = 80**, volume relatif `Up x.x dB` refusé → lire/calculer/écrire
- **RC=3 = occupé** : ~3-5 s de digestion après un changement de source réseau,
  retry rapprochés contre-productifs → délai 5 s
- Tuner : chemin `Tuning/Freq/{FM,AM}`, pas d'auto-search YNC, presets sans `<Val>`
- Réseau sous `System/Misc/Network/` (pas `System/Config/`)
- ❌ Non supportés en YNC : Sleep, Gapless, Device_Name, FM_Mode, Jump_Line,
  Track_Number, Balance, Tone, Speaker A/B → façade ou télécommande IR physique uniquement

### Remote_Signal — IR virtuel

```xml
<YAMAHA_AV cmd="PUT"><System><Misc><Remote_Signal><Receive>
  <Code>7F0146B9</Code>
</Receive></Remote_Signal></Misc></System></YAMAHA_AV>
```

Seul le device **7F01** est décodé. Codes connus : `0x46` Shuffle, `0x45` Repeat
(utilisés par l'app officielle), `0x3A`/`0x3F` sources réseau.
**Reste à capturer** : les codes de la télécommande RAX31 (volume, power,
Speaker A/B, Balance...) via récepteur IR — si leur device ≠ 7F01, il faudra un
émetteur IR physique (ESP32 + LED) pointé sur la façade.

## 📁 Arborescence

```
├── rn301/               # ⭐ la librairie (rn301.py = client YNC complet)
├── webapp/              # ⭐ serveur web + page de contrôle + API JSON
├── docs/
│   ├── YNC_COMMANDS.md  # référence des commandes validées en live
│   ├── RESEARCH.md      # journal de reverse engineering
│   └── ops_brut.txt     # table des ~90 ops YNC extraites de l'APK
├── tools/               # scripts d'analyse (androguard, sondage, scan IR)
└── amp/                 # réponses live archivées (probe_gets.json, scans IR)
```

Fichiers **non distribués** (propriété Yamaha/tiers, gardés en local,
gitignorés) : l'APK *NP Controller v4.60* (téléchargeable sur APKPure),
le manuel `R-N301_om_G.pdf`, la doc `Remote_Control_Code.pdf`, `manual.txt`.
Les scripts de `tools/` s'attendent à trouver l'APK à la racine du projet.

## ⚠️ Avertissements

- Projet amateur non affilié à Yamaha. YNC est non documenté officiellement.
- Le firmware R-N301 est capricieux : toujours prévoir des retries (la lib le fait).
- `System/Misc/Update` peut déclencher une mise à jour firmware : à n'utiliser
  qu'en connaissance de cause.
- NET RADIO : le service vTuner intégré au R-N301 est en fin de vie (compte
  payant / stations réduites) — c'est côté Yamaha/vTuner, pas côté protocole.

## 📄 Licence

Code du projet : libre (faites-vous plaisir). L'APK et les PDF Yamaha restent
la propriété de Yamaha — ne pas redistribuer.
