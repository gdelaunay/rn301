# Référence YNC — Yamaha R-N301

Endpoint : `POST http://192.168.1.91/YamahaRemoteControl/ctrl`
Enveloppe : `<YAMAHA_AV cmd="GET|PUT">…</YAMAHA_AV>` — lecture de valeur : texte `GetParam`.

## Quirks du firmware R-N301 (Network_Player/3.1)
1. **Une seule requête par POST** : plusieurs chemins combinés → HTTP 400 corps vide.
2. **Chemins exacts uniquement** : les GET de sous-éléments échouent souvent
   (ex `<Play_Info><Station_Name>GetParam` → 400, `Volume/Lvl/Val` GET → 400 aléatoire).
   → GET du bloc complet (`Basic_Status`, `Play_Info`) et parsing. C'est ce que fait la lib.
3. **Instable quand occupé** : réponses vides/400 aléatoires si l'ampli est sollicité
   ailleurs → toujours prévoir retry (3×).
4. **RC=3 = "occupé/pas prêt"** : après un changement de source réseau, l'ampli met
   ~3-5 s avant d'accepter une nouvelle commande. Des retries trop rapprochés
   semblent entretenir l'état occupé → la lib retry avec délai de 5 s.
5. Orthographe des sources (Input_Sel) : `NET RADIO` (avec espace !), `SERVER`,
   `Spotify`, `AirPlay`, `TUNER` (majuscules — `Tuner` est refusé RC=3), `CD`,
   `OPTICAL`, `COAXIAL`, `LINE1..3`. Tags XML des sources réseau :
   `NET_RADIO` (pas `Net_Radio` !), `SERVER`, `Spotify`, `AirPlay`, `Tuner`.
6. Pas de **descripteur YNC** sur ce modèle (`/YamahaRemoteControl/desc.xml`
   → 404, `UnitDesc.xml` vide, pas de `GETDESC`) → les libs YNC génériques
   (`rxv`, pilotées par `desc.xml`) ne peuvent pas fonctionner. Le
   `desc.xml` **UPnP** du renderer `:8080` existe (`amp/upnp_desc.xml`) et son
   bloc vendor `<yamaha:X_device>` donne `X_URLBase` + `X_controlURL
   /YamahaRemoteControl/ctrl` → découverte SSDP possible sans IP fixe
   (implémentée : `rn301.discover()` / `discover_one()`).
7. **Volume : échelle protocole 0..100, identique** sur les deux canaux (YNC et
   UPnP — mesuré en live au plafond : 80 = 80). Le plafond effectif vient du
   réglage « Max Volume » de l'ampli (modifiable en façade, **aucun chemin YNC
   de lecture**) : un `PUT` de 100 est clampé par le firmware. Le
   `allowedValueRange 0..100` du SCPD RenderingControl est **déclaratif**.
   La lib travaille sur 0..100 (le firmware clampe) + `RN301(max_volume=N)`
   optionnel (plafond logiciel). Volume relatif
   `Up/Down N.0 dB` **refusé** (RC=3) → lire + calculer + écrire en absolu.
   Le SCPD RenderingControl (`amp/upnp_rendering.xml`) expose aussi
   `SetVolume`/`GetVolume`/`SetMute` → second canal de contrôle possible,
   **mêmes valeurs** que le YNC — non exploité par la lib (une seule voie
   d'écriture ; les *events* volume/mute arrivent par GENA).
8. **Non supportés en YNC** (HTTP 400, validé en live) : `System/Config/Sleep`,
   `Gapless_Playback`, `Device_Name`, `Tuner FM_Mode`, `Search_Mode` (auto-search),
   tuning `Up/Down` (fréquences absolues uniquement), preset avec wrapper `<Val>`
   (valeur directe OK, `Up/Down` OK aussi).
9. Chemins réseau réels : `System/Misc/Network/Network_Standby`, `Network_Name`,
   `MAC_Address_Filter` (Mode + `Address/Number_1..6`) — PAS sous `System/Config`
   ni `System/Network`.

## ✅ GET validés en live (résultats dans amp/probe_gets.json)

| Fonction | Requête (dans `<YAMAHA_AV cmd="GET">`) | Notes |
|---|---|---|
| Tout l'état zone | `<Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>` | power+volume+mute+input+icône |
| Power zone | `<Main_Zone><Power_Control><Power>GetParam</Power>…` | On / Standby |
| Volume | `<Main_Zone><Volume><Lvl>GetParam</Lvl></Volume>…` | Val échelle protocole (plafond = réglage « Max Volume » façade, 80 sur cet appareil ; 0 atteint en live) |
| Mute | `<Main_Zone><Volume><Mute>GetParam</Mute>…` | On/Off/Attenuation? |
| Input courant | `<Main_Zone><Input><Input_Sel>GetParam</Input_Sel>…` | |
| Liste inputs | `<Main_Zone><Input><Input_Sel_Item>GetParam</Input_Sel_Item>…` | 11 entrées + icônes |
| Config système | `<System><Config>GetParam</Config></System>` | model, version, features |
| Info NET_RADIO | `<NET_RADIO><Play_Info>GetParam</Play_Info></NET_RADIO>` | Station/Album/Song, Album_ART URL |
| Info SERVER | `<SERVER><Play_Info>GetParam</Play_Info></SERVER>` | + Play_Mode (Repeat/Shuffle) |
| Liste SERVER | `<SERVER><List_Info>GetParam</List_Info></SERVER>` | navigation DLNA (Line_1..8, Cursor) |
| Info Spotify | `<Spotify><Play_Info>GetParam</Play_Info></Spotify>` | |
| Info AirPlay | `<AirPlay><Play_Info>GetParam</Play_Info></AirPlay>` | |
| Info Tuner | `<Tuner><Play_Info>GetParam</Play_Info></Tuner>` | Band/Freq/Preset/FM_Mode/Signal/RDS |

Play_Info répond même quand la source est inactive (`Feature_Availability: Not Ready`).

## ✅ PUT validés en live

| Fonction | Requête (dans `<YAMAHA_AV cmd="PUT">`) | Valeurs |
|---|---|---|
| Power zone | `<Main_Zone><Power_Control><Power>On</Power>…` | On / Standby |
| Volume absolu | `<Main_Zone><Volume><Lvl><Val>54</Val><Exp>0</Exp><Unit></Unit></Lvl>…` | Val 0..100, clampé par le firmware à son « Max Volume » (échelle identique au canal UPnP) |
| Mute | `<Main_Zone><Volume><Mute>On</Mute>…` | On / Off |
| Input | `<Main_Zone><Input><Input_Sel>SERVER</Input_Sel>…` | voir liste des 11 inputs |
| Playback | `<{SRC}><Play_Control><Playback>Play</Playback>…` | Play/Stop/Pause/Skip Fwd/Skip Rev — SRC ∈ NET_RADIO, SERVER, Spotify, AirPlay |
| Shuffle/Repeat SERVER | `<SERVER><Play_Control><Play_Mode><Shuffle>` / `<Repeat>` | On/Off ; Off/One/All |
| Navigation liste | `<{SRC}><List_Control><Cursor>` | Up/Down/Left/Right/Return/Sel (NET_RADIO, SERVER) |
| Page liste | `<{SRC}><List_Control><Page>` | Up/Down |
| Ligne directe | `<{SRC}><List_Control><Direct_Sel><Line_N>` | Line_1..8 |
| Tuning FM | `<Tuner><Play_Control><Tuning><Freq><FM><Val>9910</Val><Exp>2</Exp><Unit>MHz</Unit>` | **chemin Freq/FM** (pas FM/Freq !) |
| Tuning AM | `<Tuner><Play_Control><Tuning><Freq><AM><Val>1197</Val><Exp>0</Exp><Unit>kHz</Unit>` | kHz |
| Bande | `<Tuner><Play_Control><Tuning><Band>FM</Band>` | FM / AM (GET refusé, lire via Play_Info) |
| Preset tuner | `<Tuner><Play_Control><Preset><Preset_Sel>3</Preset_Sel>` | 1..40 **ou** Up/Down (pas de `<Val>`) |
| Network standby | `<System><Misc><Network><Network_Standby>On</Network_Standby>` | On / Off |
| Nom réseau | `<System><Misc><Network><Network_Name>` | texte |
| Filtre MAC | `<System><Misc><Network><MAC_Address_Filter><Mode>` + `<Address><Number_1..6>` | Off/… |
| Injection IR | `<System><Misc><Remote_Signal><Receive><Code>7F0146B9</Code></Receive></Remote_Signal></Misc></System>` | voir section Remote_Signal |

## ❌ PUT refusés par le firmware (validé en live, HTTP 400 ou RC=3)

Sleep, Gapless_Playback, Device_Name (System/Config/*), FM_Mode, Search_Mode,
tuning Up/Down, NET_RADIO Play_Mode (Shuffle/Repeat → passer par Remote_Signal),
`Tuner/Play_Control/Tuning/Band` en GET, `System/Network/*`, preset `<Val>` wrapper.

## ⚠️ SERVER (DLNA) : `Pause` accepté mais **ignoré** — mesuré en live

Pendant une écoute SERVER (musique en cours, `Play_Time` qui tourne) :

| PUT `<SERVER><Play_Control><Playback>` | RC | `Playback_Info` après | effet |
|---|---|---|---|
| `Pause` | **0** | `Play` (inchangé, `Play_Time` 107→115→152) | **aucun** |
| `Stop` | **0** | `Stop`, `Play_Time=0`, méta vidées | honoré |
| `Play` (après Stop) | **0** | `Stop` (inchangé) | **aucun** — la file est vidée par Stop, il faut re-sélectionner une ligne (`List_Control/Direct_Sel`) |

Contrôle : `Play` envoyé **pendant** la lecture renvoie aussi `RC="0"` → sur ce
firmware, `RC="0"` signifie « syntaxe acceptée », pas « ordre exécuté ». Aucune
erreur n'est donc remontable côté client.

Confirmé dans la même session : l'instance AVTransport UPnP de l'ampli déclare
`NO_MEDIA_PRESENT` / `rel_time 0:00:00` pendant que `SERVER/Play_Info` dit
`Play` → les deux piles sont bien étanches (déjà mesuré, cf. RESEARCH.md).
L'app Yamaha officielle envoie le même op et échoue pareil. Ce n'est donc **pas**
le serveur DLNA : l'ampli ne change même pas son propre état, il ne lui parle pas.

Conséquence UI : dans la webapp, le bouton lecture/pause est **grisé** en input
SERVER hors mode push (`title` = « Pause non supporté par le firmware… »). En
mode push, Pause fonctionne réellement (AVTransport, reprise à la position
exacte — cf. DLNA_PUSH.md). NET RADIO / Spotify / AirPlay : **non testés**,
comportement inchangé.



## 🔧 Jump_Line / Track_Number : NON supportés (testés en live → 400)

Présents dans k.java/q.java (docs/ops_brut.txt = table des ~90 opérations) mais
refusés par le firmware R-N301 — spécifiques à d'autres modèles de l'app
(CD-N301, R-N500, CRX-560...). Idem pour tout ce qui est listé dans la section
« PUT refusés » ci-dessus.

## 📦 Librairie Python (`rn301/`) + serveur web (`webapp/`)

```python
from rn301 import RN301, discover_one
amp = RN301(discover_one()["host"])  # découverte SSDP — ou IP fixe en dur
amp.power_on()                       # retries + délai RC=3 gérés
amp.set_volume(50)                   # 0..100, l'ampli clampe à son Max Volume
amp.set_input("TUNER")               # majuscules !
amp.tune_fm(99.1)
amp.tuning_step("Up")           # calculé côté client (absolus uniquement)
amp.select_preset(3)            # ou 'Up'/'Down'
amp.playback("SERVER", "Play")
amp.select_line("NET RADIO", 5)
amp.remote_key("shuffle")       # IR virtuel 7F0146B9
print(amp.basic_status())       # power/volume/mute/input (bloc fiable)
print(amp.play_info("current"))
print(amp.list_info("SERVER")) # menu DLNA 8 lignes + curseur
```

Push DLNA + événements GENA + découverte : voir `docs/DLNA_PUSH.md`.

Serveur web (page de contrôle + API JSON) :
```
.venv/Scripts/python -m webapp.server --port 8080
# -> http://localhost:8080
```
Routes API : `/api/state`, `power`, `volume` (val|delta), `mute`, `input`,
`playback`, `playmode`, `list`, `cursor`, `page`, `line`, `tuner`
(op: tune|band|step|preset|preset_step), `netstandby`, `netname`, `macfilter`,
`remote` (key|code), `raw` (cmd+xml), `systempower`, `update`, GET `/art?url=`.

## 📡 Remote_Signal (injection de touches télécommande)

```xml
<YAMAHA_AV cmd="PUT"><System><Misc><Remote_Signal><Receive>
  <Code>7F0146B9</Code>
</Receive></Remote_Signal></Misc></System></YAMAHA_AV>
```

- **Format** : `DEVICE(2) SUB(2)... ` → 4 octets hex : `7F 01 OBC ~OBC`
  (NEC étendu : OBC + complément à 0xFF obligatoire)
- **Seul le device `7F01` est décodé** par le R-N301. Testés sans effet :
  `7A01` (256 OBC), `7A85`, `7E85`, `7E01`, `7F85`, `7F00`, ordres inversés...
- **Codes confirmés** (device 7F01) :
  | OBC | Code complet | Effet |
  |---|---|---|
  | 0x3A | 7F013AC5 | source réseau (cycle/NET RADIO — à préciser) |
  | 0x3F | 7F013FC0 | **cycle sources réseau** : SERVER→NET RADIO→AirPlay→Spotify→… |
  | 0x45 | 7F0145BA | Repeat (utilisé par l'app officielle) |
  | 0x46 | 7F0146B9 | Shuffle (utilisé par l'app officielle) |
- Scan complet des 256 OBC de 7F01 : aucun effet observable power/volume/mute/input
  en dehors de 0x3A/0x3F. **Les effets non observables via YNC (Speaker A/B,
  Balance, menus) n'ont pas pu être exclus pendant ce scan** → rescan visuel prévu.
- Parfois un inject échoue silencieusement → retry.

## 🔜 En attente : capture IR de la télécommande RAX31 (ZN04320)
Objectif : connaître le device code + OBC réels des touches ampli
(SPEAKERS A/B, BALANCE via Setup, VOLUME, POWER) et vérifier si elles
sont injectables via Remote_Signal (si device ≠ 7F01, probablement non
→ il faudra un émetteur IR physique type ESP32).

## Sources
- `docs/RESEARCH.md` — historique de la recherche
- `docs/ops_brut.txt` — table brute des ~90 opérations de l'app (k.java)
- `apk/decompiled/src/com/yamaha/npcontroller/j/q.java` — builder XML réel
- `amp/probe_gets.json` — réponses live des GET
- `amp/remote_scan*.json` — résultats des scans Remote_Signal
