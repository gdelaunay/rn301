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
services :
  RenderingControl  /RenderingControl/ctrl    (volume/mute UPnP, en plus du YNC)
  ConnectionManager /ConnectionManager/ctrl   (GetProtocolInfo)
  AVTransport       /AVTransport/ctrl         (SetAVTransportURI/Play/Pause/Stop/GetTransportInfo)
```

L'APK contient aussi les pistes `:49154/MediaRenderer/desc.xml` et
`:50100/YamahaRemoteControl/dmr.xml` (autres firmwares/modèles) — sur notre
R-N301 seul le **8080** répond.

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

### TODO / reste à tester

- FLAC / MP4-AAC / WMA malgré la liste Sink restrictive (l'app les déclare)
- Push d'une **webradio MP3** (`push_url` avec flux http direct)
- Enchaînement de pistes (re-pousser à la fin via polling `STOPPED`)
- `SetNextAVTransportURI` : absent de l'APK, probablement pas dans le SCPD
  (`GET :8080/AVTransport/desc.xml` à vérifier)
- Volume UPnP via `RenderingControl` (double pilotage YNC/UPnP)

## 5. Ce que ça débloque

- **Jouer n'importe quel fichier du PC/téléphone** sur la chaîne, sans NAS ni
  MiniDLNA — la webapp devient serveur + télécommande.
- **Stream généré à la volée** : ffmpeg (transcodage, mix, TTS, webradios
  non-vTuner…) → tant que ça sort en MP3/WAV sur un port HTTP, l'ampli joue.
- **Contourner vTuner** pour les webradios : pousser directement l'URL du
  flux (si le format passe) au lieu de passer par NET RADIO.
- Volume/mute disponibles aussi en UPnP (`RenderingControl`) — double pilotage
  YNC + UPnP possible.
