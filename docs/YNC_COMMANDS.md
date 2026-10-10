# Référence YNC — Yamaha R-N301

Endpoint : `POST http://192.168.1.91/YamahaRemoteControl/ctrl`
Enveloppe : `<YAMAHA_AV cmd="GET|PUT">…</YAMAHA_AV>` — lecture de valeur : texte `GetParam`.

## Quirks du firmware R-N301 (Network_Player/3.1)
1. **Une seule requête par POST** : plusieurs chemins combinés → HTTP 400 corps vide.
2. **Chemins exacts uniquement** : les GET de sous-éléments échouent souvent
   (ex `<Play_Info><Station_Name>GetParam` → 400). GET du bloc complet et parsing.
3. **Instable quand occupé** : réponses vides/400 aléatoires si l'ampli est sollicité
   ailleurs → toujours prévoir retry (3×, 500 ms).
4. Orthographe des sources : `NET_RADIO`, `SERVER`, `Spotify`, `AirPlay`, `Tuner`,
   `iPod_USB`, `USB` (tel que renvoyé par Input_Sel_Item).
5. Pas de desc.xml / UnitDesc.xml / GETDESC sur ce modèle.

## ✅ GET validés en live (résultats dans amp/probe_gets.json)

| Fonction | Requête (dans `<YAMAHA_AV cmd="GET">`) | Notes |
|---|---|---|
| Tout l'état zone | `<Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>` | power+volume+mute+input+icône |
| Power zone | `<Main_Zone><Power_Control><Power>GetParam</Power>…` | On / Standby |
| Volume | `<Main_Zone><Volume><Lvl>GetParam</Lvl></Volume>…` | Val 0..100? (51 observé, max à déterminer) |
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
| Volume absolu | `<Main_Zone><Volume><Lvl><Val>54</Val><Exp>0</Exp><Unit></Unit></Lvl>…` | Val numérique |
| Volume relatif | …`<Val>Down 1.0 dB</Val>`… | Up/Down 1.0 dB (syntaxe communauté, à re-valider) |
| Mute | `<Main_Zone><Volume><Mute>On</Mute>…` | On / Off |
| Input | `<Main_Zone><Input><Input_Sel>SERVER</Input_Sel>…` | voir liste des 11 inputs |
| Injection IR | `<System><Misc><Remote_Signal><Receive><Code>7F0146B9</Code></Receive></Remote_Signal></Misc></System>` | voir section Remote_Signal |

## 🔧 PUT connus via code de l'app (non testés — prudence)

Issus de k.java/q.java (docs/ops_brut.txt = table complète des ~90 opérations) :

| Fonction | Chemin PUT |
|---|---|
| Playback | `<{source}><Play_Control><Playback>` : Play/Stop/Pause/Skip Fwd/Skip Rev |
| Mode lecture | `<{source}><Play_Control><Play_Mode><Shuffle>` / `<Repeat>` : On/Off, One/All |
| Navigation liste | `<{source}><List_Control><Cursor>` : Up/Down/Left/Right/Return/Sel |
| Page liste | `<{source}><List_Control><Page>` : Up/Down |
| Ligne directe | `<{source}><List_Control><Direct_Sel><Line_N>` |
| Saut index | `<{source}><List_Control><Jump_Line><Line>` : entier |
| Tuning FM | `<Tuner><Play_Control><Tuning><FM><Freq><Val>` (Exp=2, ex 9910 = 99.10 MHz) |
| Tuning AM | `<Tuner><Play_Control><Tuning><AM><Freq><Val>` (Exp=0, kHz) |
| Preset tuner | `<Tuner><Play_Control><Preset><Preset_Sel><Val>` : 1..40? |
| Sleep | `<System><Config><Sleep>` : Off/30/60/90/120 min ? |
| Network standby | `<System><Config><Network_Standby>` : On/Off |
| Gapless | `<System><Config><Gapless_Playback>` : On/Off |
| Nom réseau | `<System><Config><Device_Name>` |
| Filtre MAC | `<System><Network><MAC_Address_Filter><Mode>` + `<Address_1..5>` |
| Mise à jour firmware | `<System><Misc><Update><Yamaha_Network_Site>` |

⚠️ Certains sont peut-être spécifiques à d'autres modèles de l'app (CD-N301, R-N500,
CRX-560...) : valider un par un avant usage (le R-N301 répond 400 si non supporté).

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
