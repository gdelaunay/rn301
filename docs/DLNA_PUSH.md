# 🔊 DLNA Push — envoyer n'importe quel audio au R-N301

> **✅ VALIDÉ EN LIVE** (2025-10-10) : WAV et MP3 poussés depuis le PC via
> `rn301/push.py` → l'ampli fait bien `GET` sur notre serveur HTTP et joue.
> Pause/reprise OK (rel_time conservé). **Seek refusé** par le renderer
> ("Seek mode not supported"). Piège trouvé en live : déclarer
> `DLNA.ORG_OP=01` (seekable) sur un WAV alors que le renderer ne sait pas le
> gérer → `ERROR_OCCURRED` à la reprise après pause. L'app officielle déclare
> `OP=00` pour wav/flac/mp3/wma et `OP=01` seulement pour mp4/3gpp —
> `rn301/push.py` recopie ce mapping.

Découverte majeure : en plus du YNC (port 80), le R-N301 expose un
**MediaRenderer UPnP/DLNA complet** — on peut lui *pousser* directement une URL
audio, exactement comme le fait l'app officielle avec la musique du téléphone.

## 1. Le renderer UPnP de l'ampli (validé en live)

Contrairement au desc.xml YNC (`/YamahaRemoteControl/desc.xml` → 404), le
renderer est bien présent, sur le **port 8080** :

```
GET http://192.168.1.91:8080/MediaRenderer/desc.xml      → 200 OK
```

```
friendlyName : YAMAHA R-N301
deviceType   : urn:schemas-upnp-org:device:MediaRenderer:1  (DMR-1.50)
UDN          : uuid:5f9ec1b3-ed59-1900-4530-00a0dec48d64
SERVER       : KnOS/3.2 UPnP/1.0 DMP/3.5   (stack UPnP embarquée)
services :              ctrl                    SCPD (contrat)         evt (GENA)
  RenderingControl   /RenderingControl/ctrl   /RenderingControl/desc.xml   /RenderingControl/evt
  ConnectionManager  /ConnectionManager/ctrl  /ConnectionManager/desc.xml  /ConnectionManager/evt
  AVTransport        /AVTransport/ctrl        /AVTransport/desc.xml        /AVTransport/evt
```

Le `<yamaha:X_device>` du desc.xml contient en plus `X_URLBase
http://<ip>:80/` + `X_controlURL /YamahaRemoteControl/ctrl` : **l'endpoint YNC
est référencé depuis l'UPnP**, ce qui permet une découverte SSDP complète sans
IP fixe. Icônes officielles servies par l'ampli : `:8080/BCO_device_{sm,lrg}_icon.{jpg,png}`
(48×48 et 120×120). `presentationURL http://<ip>/` = la page de configuration
embarquée (nom réseau, IP, mise à jour firmware).

L'APK contient aussi les pistes `:49154/MediaRenderer/desc.xml` et
`:50100/YamahaRemoteControl/dmr.xml` (autres firmwares/modèles) — sur notre
R-N301 seul le **8080** répond.

### SCPD AVTransport — le contrat réel du service (capturé le 2025-10-10)

`GET :8080/AVTransport/desc.xml` → 200, 14 973 o, archivé dans
`amp/upnp_avtransport.xml`. Reproductible avec `tools/probe_gena.py`.

**15 actions déclarées** (celles en gras ne sont pas exploitées par `push.py`) :

| Action | Remarque |
|---|---|
| `SetAVTransportURI` | utilisé |
| **`SetNextAVTransportURI`** | **bien présent dans le SCPD** (contrairement à ce que supposait le TODO) mais sans effet en pratique → stub firmware, donc pas de gapless |
| `GetMediaInfo` `GetTransportInfo` `GetPositionInfo` | utilisés (`state()`) |
| **`GetDeviceCapabilities`** | live : `PlayMedia=NONE,NETWORK`, `RecMedia=NOT_IMPLEMENTED` |
| **`GetTransportSettings`** | live : `PlayMode=NORMAL` |
| **`GetCurrentTransportActions`** | live : `Play,Next,Previous` → dit quelles actions sont valides *maintenant*, plus besoin de deviner |
| `Stop` `Play` `Pause` | utilisés |
| **`Seek`** | déclaré avec `SeekMode = TRACK_NR, REL_TIME` — **le SCPD prétend donc supporter le seek en temps relatif**, alors que le firmware renvoie SOAP 500 « Seek mode not supported ». Le refus vient très probablement du `DLNA.ORG_OP=00` déclaré dans le DIDL (format annoncé non seekable), pas de l'action elle-même. Piste à retester. |
| **`Next` `Previous`** | existent → crochets naturels pour une file d'attente côté client |
| **`SetPlayMode`** | déclaré, mais `CurrentPlayMode` n'autorise que **`NORMAL`** → **pas de shuffle/repeat en UPnP**, le YNC reste indispensable pour ça |

**Variables d'état qui verrouillent le design :**

```
LastChange            string   EVENTED   ← la seule variable notifiée (voir §GENA)
TransportState        string   STOPPED, PLAYING, PAUSED_PLAYBACK,
                               TRANSITIONING, NO_MEDIA_PRESENT
TransportStatus       string   OK, ERROR_OCCURRED
PlaybackStorageMedium string   NETWORK                     (unique)
CurrentPlayMode       string   NORMAL                      (unique)
TransportPlaySpeed    string   1                           (unique, pas d'avance rapide)
NumberOfTracks        ui4      range 0..1                  ← UNE seule piste
CurrentTrack          ui4      range 0..1
A_ARG_TYPE_SeekMode   string   TRACK_NR, REL_TIME
Record*               NOT_IMPLEMENTED
```

Conséquences : `NumberOfTracks 0..1` + `PlayMode NORMAL` unique → **aucune
playlist côté renderer**, la file d'attente doit être gérée par le client
(re-pousser un `SetAVTransportURI` à chaque fin de piste). `TransportPlaySpeed`
à `1` → pas de vitesse variable.

### Eventing GENA — ✅ fonctionne (validé en live)

Les trois services déclarent un `eventSubURL`. **Le renderer honore GENA** :

```http
SUBSCRIBE /AVTransport/evt HTTP/1.1
HOST: 192.168.1.91:8080
CALLBACK: <http://192.168.1.32:55843/cb>
NT: upnp:event
TIMEOUT: Second-300

→ HTTP 200
   SID:     uuid:525ceb08-c534-108c-8efe-81474a82edd9
   TIMEOUT: Second-300                       (honoré tel quel)
   SERVER:  KnOS/3.2 UPnP/1.0 DMP/3.5

→ NOTIFY immédiat sur le callback (l'event initial imposé par UPnP)
   NT: upnp:event   NTS: upnp:propchange   SEQ: 0
   <e:propertyset><e:property><LastChange>
     &lt;Event xmlns="urn:schemas-upnp-org:metadata-1-0/AVT/"&gt;
       &lt;InstanceID val="0"&gt;
         &lt;TransportState val="STOPPED"/&gt;
         &lt;TransportStatus val="ERROR_OCCURRED"/&gt;
         &lt;PlaybackStorageMedium val="NETWORK"/&gt; …
   (LastChange = XML **échappé** dans une balise XML → double décodage)

UNSUBSCRIBE + SID → 200
```

Points d'implémentation relevés :

- `LastChange` est la **seule** variable `sendEvents="yes"` ; elle contient un
  document XML *échappé* (`&lt;Event…&gt;`) qu'il faut re-parser après
  décodage. Les variables individuelles (`TransportState`…) ne sont pas
  notifiées séparément.
- Le callback doit répondre **200** au `NOTIFY`, sinon l'appareil considère
  l'abonné mort. Il doit être joignable depuis l'ampli (IP LAN, pare-feu).
- `TIMEOUT: Second-300` est accepté tel quel → prévoir le **renouvellement**
  du SID avant expiration (`SUBSCRIBE` avec `SID` au lieu de `NT`/`CALLBACK`).
- `SEQ` s'incrémente à chaque NOTIFY ; un trou de séquence = events perdus.

**Non implémenté dans ce projet** : ni `rn301/push.py` ni `webapp/server.py`
n'utilisent GENA aujourd'hui (la webapp poll toujours toutes les 4 s). C'est la
prochaine étape logique : attendre la fin d'une piste ou d'une annonce TTS sur
un event `STOPPED` au lieu de poller `transport_info()`.

### Couplage YNC ↔ UPnP : ce qui est notifié, ce qui ne l'est pas

**Mesuré en live le 2025-10-10** (`tools/probe_gena_raw.py`, fenêtre de 420 s,
manipulations au bouton physique de façade + télécommande, polling YNC de
`Basic_Status` toutes les 0,5 s en parallèle pour corréler les deux piles).

27 NOTIFY `RenderingControl` reçus, dont 26 provoqués par la manipulation :

```
t=  0.09s SEQ=0    Mute=0, Volume=50, PresetNameList=…   ← état complet (initial)
t= 37.71s SEQ=1    Volume=52        ┐
t= 79.46s SEQ=17   Volume=80        │  bouton physique / télécommande
t= 82.58s SEQ=18   Volume=0         │
t= 96.45s SEQ=21   Mute=1           │
t= 99.81s SEQ=22   Mute=0           ┘
t=160.14s SEQ=26   Volume=31
```

Latence : l'event `Volume=80` arrive à **79,46 s**, le polling YNC ne voit `80`
qu'à **79,49 s** → GENA est ~30 ms **plus rapide** que la sonde. C'est ce qui
explique la réactivité de l'app officielle sur le volume.

#### ✅ Ce que GENA notifie

| Changement | Service | Observé |
|---|---|---|
| Volume (bouton façade, télécommande) | `RenderingControl` | ✅ `Volume[Master]` |
| Mute on/off | `RenderingControl` | ✅ `Mute[Master]=1` puis `=0` |
| Transport du **push DLNA** | `AVTransport` | ✅ `TransportState` (voir §GENA) |

#### ❌ Ce que GENA ne voit **jamais**

| Manipulation effectuée | Events reçus |
|---|---|
| `Input_Sel` LINE1 → COAXIAL → LINE1 (t=47,9 s) | **0** |
| `Input_Sel` LINE1 → LINE2 → LINE1 (t=104,5 s) | **0** |
| `Input_Sel` LINE1 → SERVER (t=133,9 s) | **0** |
| **`Power` On → Standby → On** (t=110,5 s) | **0** |
| Lecture réseau SERVER + Play/Pause | **0** sur AVTransport |

Sur 420 s, `AVTransport` n'a émis **qu'un seul** NOTIFY : l'event initial. Le
renderer ne reflète que le push DLNA, jamais la lecture réseau pilotée en YNC —
ce qui confirme par la mesure la séparation déjà documentée au §3 (« le push ne
change pas l'entrée YNC »). Symétriquement, un `Play`/`Pause` envoyé à
l'AVTransport pendant une écoute `SERVER` n'a **aucun effet** : il faut passer
par le YNC (`Play_Control/Playback`).

**Conclusion d'architecture : GENA ne remplace pas le polling YNC, il le
complète.** Volume/mute en push instantané, tout le reste (power, source,
play_info, navigation des listes, tuner) reste à poller.

#### ⭐ Échelle de volume : UPnP == YNC (question tranchée)

Volume poussé **au maximum** pendant la mesure :

| canal | valeur au plafond |
|---|---|
| GENA `Volume[Master]` | **80** |
| YNC `Basic_Status/Val` | **80** |
| SCPD `allowedValueRange` | 0..100 *(déclaratif, non appliqué)* |

Les deux échelles sont **identiques** : aucun mapping à écrire. Le `0..100` du
SCPD est une déclaration générique ; le plafond réel (80 ici) vient du réglage
« Max Volume » de l'ampli, modifiable en façade. Conséquence : `VOL_MAX` ne
doit pas être codé en dur — si l'utilisateur monte ce réglage à 100, une lib
bridée à 80 serait fautive.

#### ⚠️ Trois pièges d'implémentation

1. **`LastChange` est incrémental.** Seul `SEQ=0` contient l'état complet
   (`PresetNameList` + `Mute` + `Volume`) ; les events suivants ne portent
   **que la variable modifiée** (`SEQ=1..20` → uniquement `Volume`). Il faut
   donc maintenir un état local et appliquer des deltas, pas écraser l'objet.
2. **Les events sont modérés (coalescés).** UPnP autorise l'appareil à regrouper
   les notifications : en tournant vite le bouton, le polling YNC a vu
   `52→55→49→45…` tandis que GENA envoyait `52→53→45→41…` — 26 events pour
   davantage de pas réels, avec des valeurs intermédiaires différentes. GENA
   donne la **valeur finale fiable**, pas un historique exhaustif. Parfait pour
   un slider d'UI, insuffisant pour tracer chaque pas.
3. **Attributs dans l'ordre inverse.** Le XML est
   `<Volume channel="Master" val="50"/>` : `channel` **avant** `val`. Un parseur
   qui attend `val=` immédiatement après le nom de balise ne voit rien (bug
   rencontré pendant la mesure : les events semblaient vides alors qu'ils ne
   l'étaient pas).

### `TransportStatus = ERROR_OCCURRED` au repos — explication

Observé ampli « au repos » : `GetTransportInfo` → `CurrentTransportState =
STOPPED` **et** `CurrentTransportStatus = ERROR_OCCURRED`, alors que
`GetMediaInfo` renvoie encore l'URI du dernier push :

```
CurrentURI     http://192.168.1.32:9000/audio/1/
MediaDuration  0:04:12
NrTracks       1
```

→ Le renderer **conserve la dernière URI poussée** après l'arrêt de notre
serveur de fichiers. Il tente de la recharger, n'y arrive plus, et passe en
`ERROR_OCCURRED`. Ce n'est donc pas un état d'erreur « actif » mais une URI
périmée. Conséquences :

- ne jamais interpréter `ERROR_OCCURRED` seul comme un échec du push en cours :
  toujours le croiser avec `TransportState` et l'URI courante ;
- un `SetAVTransportURI` valide le ramène à `OK` ;
- à la fermeture du serveur de push, envoyer `Stop` (voire re-pousser une URI
  vide) pour ne pas laisser l'ampli en erreur ;
- `MediaDuration` survit au serveur de fichiers → l'ampli met en cache les
  métadonnées, la webapp peut les relire pour afficher la dernière piste.

### Formats acceptés en push (GetProtocolInfo → Sink)

```
audio/L16;rate=44100;channels=1|2   (LPCM, DLNA.ORG_PN=LPCM)
audio/L16;rate=48000;channels=1|2   (LPCM)
audio/mpeg                          (MP3, DLNA.ORG_PN=MP3)
audio/x-ms-wma                      (WMABASE, WMAFULL)
http-wavetunes:*:audio/x-ms-wma     (WMA streaming)
```

⚠️ La liste Sink officielle n'annonce que **MP3 / WMA / LPCM(WAV)** — mais
l'app officielle envoie aussi dans ses DIDL du `audio/flac`, `audio/wav`,
`audio/mp4`, `audio/3gpp` (strings APK lignes 564-569). Le décodeur du R-N301
(entrée SERVER) supporte FLAC/WAV/AAC/ALAC : le renderer accepte probablement
plus que ce qu'il déclare. **À tester format par format.**

Pour tout le reste : transcoder à la volée en WAV/LPCM ou MP3 (ffmpeg) et le
push fonctionne à coup sûr.

## 2. Comment l'app officielle fait son "module audio du téléphone"

Package décompilé : `com.yamaha.av.localfilecontroller` (22 classes,
`apk/decompiled/src/com/yamaha/av/localfilecontroller/`).

Architecture (téléphone = serveur de fichiers + point de contrôle UPnP) :

```
┌──────────────────────────────┐         ┌────────────────────────────┐
│  NP Controller (téléphone)   │         │       Yamaha R-N301        │
│                              │  SOAP   │                            │
│ p.java  (control point)  ────┼────────►│ :8080/AVTransport/ctrl     │
│   SetAVTransportURI + Play   │         │   SetAVTransportURI        │
│                              │         │         │                  │
│ h.java  ServerSocket(0)      │         │         ▼                  │
│ i.java  mini serveur HTTP ◄──┼─────────┼── GET /audio/<id>/ (Range) │
│   sert les fichiers          │  HTTP   │   l'ampli tire le flux     │
│ MediaHttpService.java :      │         │                            │
│   MediaStore.Audio (biblio)  │         │                            │
└──────────────────────────────┘         └────────────────────────────┘
```

Détails relevés dans le code décompilé :

- **`h.java`** : `new ServerSocket(0)` → port aléatoire, "Web Server is
  listening on port …", boucle `accept()` (soTimeout 1 s).
- **`i.java`** : serveur HTTP minimal. Routes regex
  `GET /audio/(.+)/` et `GET /albumart/(.+)`. Content-Type choisi par
  l'extension du fichier (wav/wave, mp3, m4a/mp4/aac, 3gp/3g2, fla/flac,
  wma/asf). Support **`Range: bytes=`** → `206 Partial Content` +
  `Content-Range` (c'est comme ça que le seek fonctionne). Header
  `TransferMode.DLNA.ORG: Streaming` et `Accept-Ranges: bytes`.
- **`p.java`** : le point de contrôle. Construit `SetAVTransportURI` avec
  `CurrentURI = http://<ip-tel>:<port>/audio/<id>/` et
  `CurrentURIMetaData` = **DIDL-Lite** (`dc:title`, `upnp:artist`,
  `upnp:album`, `upnp:albumArtURI`, `res protocolInfo="http-get:*:audio/…"`
  avec `DLNA.ORG_OP` + `DLNA.ORG_FLAGS=017…`). Puis `Play`, `Pause`, `Stop`,
  `GetTransportInfo` (lecture de `CurrentTransportState`).
- **`MediaHttpService.java`** : service Android foreground (notification),
  requête la bibliothèque via `MediaStore$Audio$Media`, playlists, album art
  (`content://media/external/audio/albumart`).

Conclusion : **rien de propriétaire** — c'est du DLNA push standard. Toute
URL audio servie en HTTP sur le LAN peut être envoyée à l'ampli.

## 3. Protocole de push (recette minimale)

```http
POST /AVTransport/ctrl HTTP/1.1
Host: 192.168.1.91:8080
Content-Type: text/xml; charset="utf-8"
SOAPAction: "urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI"

<?xml version="1.0" encoding="utf-8"?>
<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"
            xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
      <InstanceID>0</InstanceID>
      <CurrentURI>http://192.168.1.32:9000/audio/1/</CurrentURI>
      <CurrentURIMetaData>&lt;DIDL-Lite …&gt;…&lt;/DIDL-Lite&gt;</CurrentURIMetaData>
    </u:SetAVTransportURI>
  </s:Body>
</s:Envelope>
```

Puis `#Play` (`<Speed>1</Speed>`). `CurrentURIMetaData` peut rester vide
(`NOT_IMPLEMENTED`) — l'ampli joue quand même, seules les infos d'affichage
manquent.

États `GetTransportInfo` observés : `NO_MEDIA_PRESENT` (rien de chargé),
`STOPPED` / `PLAYING` / `PAUSED_PLAYBACK` après push.

Points d'attention (vus en live) :

- **Le push ne change PAS l'entrée YNC** : `Input_Sel` reste sur `SERVER`,
  `play_info` YNC reste à `Stop` pendant que le renderer joue. Les deux
  couches (YNC et UPnP) coexistent — l'état de lecture push se lit via
  `GetTransportInfo`/`GetPositionInfo`, pas via YNC.
- **`Seek` n'est pas supporté** par ce renderer (SOAP 500 "Seek mode not
  supported") → pas de seek/scrub possible, seulement Play/Pause/Stop.
  La reprise après Pause repart d'où on s'était arrêté, mais déclarer le
  format non-seekable (`DLNA.ORG_OP=00`) est impératif pour wav/flac/mp3
  sous peine d'`ERROR_OCCURRED` à la reprise.
- États observés : `NO_MEDIA_PRESENT` → `TRANSITIONING` (après SetURI/Play)
  → `PLAYING` → `PAUSED_PLAYBACK` → `STOPPED`. Fin de piste = `STOPPED`.
- L'URL doit être **joignable depuis l'ampli** (IP LAN du serveur, pas
  127.0.0.1 ; attention aux pare-feu Windows sur le port du serveur HTTP —
  l'ampli doit pouvoir atteindre le port de push, 9000 par défaut).
- Serveur HTTP : répondre à `HEAD`/`GET` avec `Content-Length` et
  `Accept-Ranges: bytes`, honorer `Range` (l'ampli envoie parfois
  `Range: bytes=N-`, répondre `206` + `Content-Range`), ne pas exiger de
  User-Agent particulier.
- RC SOAP : faute `<s:Fault>` = URI injouable (format non supporté, serveur
  injoignable…). Toujours lire le body de réponse.
- **Une seule instance à la fois** : si plusieurs serveurs de push écoutent
  sur le même port (vieilles instances non tuées), l'ampli peut tirer le
  fichier depuis une instance qui ne sert plus rien → `ERROR_OCCURRED`.

## 4. Implémentation dans le projet

- **`rn301/push.py`** — réplique fidèle de `localfilecontroller` :
  - `FileServer` : serveur HTTP mono-fichier (Range/206, HEAD, mime par
    extension) — équivalent de `h.java`+`i.java`
  - `Renderer` : client SOAP AVTransport (set_uri/play/pause/stop/
    transport_info/position_info/seek*) — équivalent de `p.java`
  - `PushServer` : façade (`push_file()`, `push_url()`, `state()`)
  - `didl_metadata()` : DIDL-Lite avec le bon `DLNA.ORG_OP` par format
  - \* `seek()` implémenté mais **refusé par ce firmware**
- **`webapp/server.py`** — routes ajoutées :
  - `POST /api/upload?name=x.wav` (body binaire brut) → sauve dans `music/`
  - `POST /api/push {file|url, title?, artist?, album?}` → sert + pousse + Play
  - `POST /api/pushctl {action: Play|Pause|Stop|Seek}` → transport
  - `GET /api/state` inclut désormais `push_state` (état renderer UPnP)
  - UI : carte "📲 Push audio" (upload fichier du navigateur/téléphone, champ
    URL webradio/NAS, boutons transport)
  - options : `--push-port` (défaut 9000), `--music` (défaut `music/`),
    `--no-push`
- **`tools/test_push.py`** — test standalone (serveur + push d'un fichier).
- **`tools/probe_gena.py`** — sonde **read-only** du renderer : dump du SCPD
  AVTransport (actions + variables d'état), capacités live
  (`GetDeviceCapabilities`, `GetCurrentTransportActions`, `GetTransportInfo`,
  `GetMediaInfo`, `GetPositionInfo`, `GetTransportSettings`) et test
  SUBSCRIBE/NOTIFY/UNSUBSCRIBE. Ne modifie aucun état de l'ampli.
- **`tools/probe_gena_raw.py`** — sonde de **couplage** : body brut horodaté de
  chaque NOTIFY `RenderingControl` + `AVTransport`, avec polling YNC de
  `Basic_Status` en parallèle. Sert à mesurer ce que GENA voit / ne voit pas
  quand on manipule l'ampli physiquement (voir §Couplage YNC ↔ UPnP).

### Tests live réalisés

| Test | Résultat |
|---|---|
| WAV LPCM 44.1k stéréo (sine 15 s) | ✅ PLAYING, l'ampli GET le fichier |
| Pause → Play | ✅ PAUSED_PLAYBACK → PLAYING, rel_time conservé |
| MP3 192k | ✅ PLAYING, durée piste correcte |
| Seek REL_TIME | ❌ SOAP 500 "Seek mode not supported" |
| DIDL avec OP=01 sur WAV | ❌ ERROR_OCCURRED à la reprise (→ OP=00) |
| push_file via webapp `/api/push` | ✅ bout en bout (upload → push → play) |
| Entrée YNC pendant le push | reste `SERVER`, play_info YNC inchangé |
| SCPD `GET :8080/AVTransport/desc.xml` | ✅ 200, 15 actions, archivé `amp/upnp_avtransport.xml` |
| `GetDeviceCapabilities` | ✅ `PlayMedia=NONE,NETWORK` |
| `GetCurrentTransportActions` | ✅ `Play,Next,Previous` |
| `GetTransportSettings` | ✅ `PlayMode=NORMAL` (seule valeur autorisée) |
| **GENA `SUBSCRIBE /AVTransport/evt`** | ✅ **200 + SID, TIMEOUT 300 honoré, 1 NOTIFY initial (SEQ 0), UNSUBSCRIBE 200** |
| GENA `RenderingControl/evt` + volume physique | ✅ 26 NOTIFY `Volume[Master]`/`Mute[Master]`, ~30 ms plus rapide que le polling YNC |
| GENA + changement d'`Input_Sel` / `Power` / lecture SERVER | ❌ **aucun event** — les deux piles sont étanches hors volume |
| Échelle de volume UPnP vs YNC, au plafond | ✅ **identiques** (80 = 80) ; le `0..100` du SCPD est déclaratif |
| `GetMediaInfo` après arrêt du serveur de fichiers | URI conservée, `TransportStatus=ERROR_OCCURRED` (voir §explication) |

### TODO / reste à tester

- FLAC / MP4-AAC / WMA malgré la liste Sink restrictive (l'app les déclare)
- Push d'une **webradio MP3** (`push_url` avec flux http direct)
- Enchaînement de pistes (re-pousser à la fin via polling `STOPPED`, ou via
  **event GENA** maintenant qu'il est validé)
- ~~`SetNextAVTransportURI` : absent du SCPD ?~~ → **vérifié : présent dans le
  SCPD mais sans effet** (stub firmware) → pas de gapless possible
- ~~`GET :8080/AVTransport/desc.xml` à vérifier~~ → **fait**, voir §SCPD
- **Implémenter GENA** dans `push.py` (serveur de callback + renouvellement SID
  + décodage `LastChange`) puis l'utiliser dans la webapp à la place du polling
- **Retester `Seek REL_TIME` avec `DLNA.ORG_OP=01`** : le SCPD déclare
  `REL_TIME` supporté, le refus observé vient peut-être du drapeau OP et non de
  l'action (mais OP=01 provoquait `ERROR_OCCURRED` à la reprise → trade-off)
- ~~Volume UPnP via `RenderingControl` (double pilotage YNC/UPnP)~~ → **mesuré** :
  échelle **identique** au YNC (80 = 80 au plafond, 0..100 du SCPD déclaratif),
  et GENA notifie les changements faits à la télécommande/au bouton physique.
  Reste à tester : un `SetVolume` **UPnP** est-il visible en YNC (et inversement),
  et le canal UPnP fonctionne-t-il pendant un RC=3 YNC ?
- Lire le réglage « Max Volume » de l'ampli au lieu de `VOL_MAX = 80` en dur
  (chemin YNC à trouver, probablement sous `Main_Zone/Volume/` ou `System/Config/`)
- `Next` / `Previous` : comportement réel sans `SetNextAVTransportURI` fonctionnel
- Capture de `/ConnectionManager/desc.xml` et `/RenderingControl/evt` (mêmes
  questions GENA pour le volume)

## 5. Ce que ça débloque

- **Jouer n'importe quel fichier du PC/téléphone** sur la chaîne, sans NAS ni
  MiniDLNA — la webapp devient serveur + télécommande.
- **Stream généré à la volée** : ffmpeg (transcodage, mix, TTS, webradios
  non-vTuner…) → tant que ça sort en MP3/WAV sur un port HTTP, l'ampli joue.
- **Contourner vTuner** pour les webradios : pousser directement l'URL du
  flux (si le format passe) au lieu de passer par NET RADIO.
- Volume/mute disponibles aussi en UPnP (`RenderingControl`) — double pilotage
  YNC + UPnP possible.
