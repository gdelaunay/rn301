# 🔊 DLNA Push — le MediaRenderer du R-N301

> **Documentation de référence** — décrit le renderer UPnP de l'ampli, le
> protocole de push et l'API de la lib. Tous les faits marqués ✅ ont été
> validés en live sur firmware 1.13/0.05. Provenance et sessions de mesure :
> [`RESEARCH.md`](RESEARCH.md).

En plus du YNC (port 80), le R-N301 expose un **MediaRenderer UPnP/DLNA
complet** sur le port **8080** : on peut lui *pousser* directement une URL
audio — exactement comme l'app officielle avec la musique du téléphone. C'est
ce que fait `rn301/push.py` (`PushServer`), avec `rn301/gena.py` pour les
événements et `rn301/discover.py` pour trouver l'ampli sans IP fixe.

```python
from rn301 import discover_one, PushServer

found = discover_one()                       # scan SSDP → IP, modèle, endpoints
push = PushServer(found["host"])
push.push_file("morceau.flac", title="Mon morceau")
push.wait_stopped(timeout=600)               # bloque sur l'event GENA de fin
push.close()
```

---

## 1. Le renderer UPnP de l'ampli

```
GET http://<ip>:8080/MediaRenderer/desc.xml      → 200 OK
```

```
friendlyName : YAMAHA R-N301
deviceType   : urn:schemas-upnp-org:device:MediaRenderer:1  (DLNA DMR-1.50)
UDN          : uuid:5f9ec1b3-ed59-1900-4530-00a0dec48d64
SERVER       : KnOS/3.2 UPnP/1.0 DMP/3.5        (stack UPnP embarquée)
```

| Service | ctrl | SCPD (contrat) | evt (GENA) | SCPD archivé |
|---|---|---|---|---|
| AVTransport | `/AVTransport/ctrl` | `/AVTransport/desc.xml` | `/AVTransport/evt` | `amp/upnp_avtransport.xml` |
| RenderingControl | `/RenderingControl/ctrl` | `/RenderingControl/desc.xml` | `/RenderingControl/evt` | `amp/upnp_rendering.xml` |
| ConnectionManager | `/ConnectionManager/ctrl` | `/ConnectionManager/desc.xml` | `/ConnectionManager/evt` | `amp/upnp_connectionmanager.xml` |

Le `<yamaha:X_device>` du desc.xml contient `X_URLBase http://<ip>:80/` +
`X_controlURL /YamahaRemoteControl/ctrl` : **l'endpoint YNC est référencé
depuis l'UPnP**. Un seul scan SSDP donne donc IP, modèle, serial, endpoint YNC,
icônes et page de config — c'est ce que fait `rn301.discover()` /
`discover_one()` (détails dans le module).

- **Icônes officielles** servies par l'ampli : `:8080/BCO_device_{sm,lrg}_icon.{jpg,png}`
  (48×48 et 120×120).
- **`presentationURL http://<ip>/`** : la page de configuration embarquée
  (nom réseau, IP, mise à jour firmware).

Contrairement au descripteur YNC (`/YamahaRemoteControl/desc.xml` → 404), ce
desc.xml UPnP existe bel et bien — c'est la seule description exploitable de
l'appareil.

---

## 2. Contrat AVTransport et limites firmware

Le SCPD (`amp/upnp_avtransport.xml`) déclare **15 actions**. Ce que le firmware
en fait réellement (validé en live) :

| Action | Comportement réel |
|---|---|
| `SetAVTransportURI` | ✅ fonctionne — charge l'URI + métadonnées DIDL |
| `Play` / `Pause` / `Stop` | ✅ fonctionnent ; reprise après Pause à la position exacte |
| `GetTransportInfo` / `GetPositionInfo` / `GetMediaInfo` | ✅ fonctionnent (`PushServer.state()`) |
| `GetDeviceCapabilities` | ✅ `PlayMedia=NONE,NETWORK` |
| `GetCurrentTransportActions` | ✅ `Play,Next,Previous` — dit quelles actions sont valides à l'instant t |
| `GetTransportSettings` | ✅ `PlayMode=NORMAL` (seule valeur autorisée) |
| `Seek` | ❌ **refusé** (SOAP 500 « Seek mode not supported ») alors que le SCPD déclare `REL_TIME` — le refus vient du `DLNA.ORG_OP=00` de nos DIDL ; avec OP=01 la reprise après pause tombe en `ERROR_OCCURRED`. Pas de seek utilisable. |
| `SetNextAVTransportURI` | ❌ **stub** : accepté (RC 200) mais sans aucun effet → pas de gapless |
| `Next` / `Previous` | déclarés ; sans piste préchargée (cf. ci-dessus), pas d'usage |
| `SetPlayMode` | ❌ seul `NORMAL` est autorisé → pas de shuffle/repeat en UPnP (passer par le YNC) |

**Variables d'état qui verrouillent le design :**

```
TransportState     STOPPED, PLAYING, PAUSED_PLAYBACK, TRANSITIONING, NO_MEDIA_PRESENT
TransportStatus    OK, ERROR_OCCURRED
NumberOfTracks     ui4, range 0..1        ← UNE seule piste, pas de playlist
TransportPlaySpeed 1 (unique)             ← pas d'avance rapide
```

Conséquences définitives :
- **Pas de file d'attente côté ampli** : l'enchaînement de pistes se fait côté
  client — attendre la fin (event GENA `STOPPED` ou `PushServer.wait_stopped()`)
  puis re-pousser un `SetAVTransportURI`. Coupure audible entre les pistes
  (gapless impossible, `SetNextAVTransportURI` étant un stub).
- **`TransportStatus = ERROR_OCCURRED` au repos est normal** : le renderer
  conserve la dernière URI poussée et tente de la recharger ; si le serveur de
  fichiers est arrêté, il reste en erreur résiduelle. Ne jamais lire
  `ERROR_OCCURRED` seul comme un échec — le croiser avec `TransportState` et
  l'URI courante. Un nouveau `SetAVTransportURI` valide ramène `OK`.
- `MediaDuration` / `CurrentURIMetaData` survivent à l'arrêt du serveur de
  fichiers → l'état renderer contient les métadonnées de la dernière piste.

**Étanchéité YNC ↔ UPnP** (mesurée) : le push ne change **pas** l'entrée YNC —
`Input_Sel` reste où il est et `play_info` YNC reste à `Stop` pendant que le
renderer joue. Il faut être sur l'entrée **SERVER** pour entendre le push.
Symétriquement, un `Play`/`Pause` envoyé à l'AVTransport pendant une écoute
SERVER (lecture réseau) n'a **aucun effet** : la lecture réseau se pilote
exclusivement en YNC (`Play_Control/Playback`).

---

## 3. Protocole de push (recette)

```http
POST /AVTransport/ctrl HTTP/1.1
Host: <ip>:8080
Content-Type: text/xml; charset="utf-8"
SOAPAction: "urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI"

<s:Envelope …><s:Body>
  <u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
    <InstanceID>0</InstanceID>
    <CurrentURI>http://192.168.1.32:9000/audio/1/</CurrentURI>
    <CurrentURIMetaData>&lt;DIDL-Lite …&gt;…&lt;/DIDL-Lite&gt;</CurrentURIMetaData>
  </u:SetAVTransportURI>
</s:Body></s:Envelope>
```

puis `#Play` (`<Speed>1</Speed>`). `CurrentURIMetaData` peut valoir
`NOT_IMPLEMENTED` — l'ampli joue quand même, seules les infos d'affichage
manquent.

**Métadonnées DIDL-Lite** (`rn301.push.didl_metadata()`) : `dc:title`,
`upnp:artist`, `upnp:album`, `upnp:albumArtURI`, et l'attribut critique du
`<res>` :

```
protocolInfo="http-get:*:<mime>:DLNA.ORG_OP=<op>;DLNA.ORG_FLAGS=01700000000000000000000000000000"
```

`DLNA.ORG_OP` par format (mapping recopié de l'app officielle) — **OP=00
impératif** pour wav/mp3/flac/wma (non seekable), OP=01 seulement pour
mp4/3gpp. Un OP=01 sur un format que le renderer ne sait pas seeker →
`ERROR_OCCURRED` à la reprise après pause.

**Serveur de fichiers** (`rn301.push.FileServer`, équivalent du téléphone dans
l'app) : l'ampli vient faire un `GET` sur l'URI poussée. Exigences :
- `GET`/`HEAD` avec `Content-Length` et `Accept-Ranges: bytes` ;
- honorer `Range: bytes=N-` → `206 Partial Content` + `Content-Range` ;
- header `TransferMode.DLNA.ORG: Streaming` ; aucun User-Agent particulier ;
- `/albumart/<n>` pour la pochette (DIDL `albumArtURI`).

**Contraintes réseau** :
- l'URL doit être joignable **depuis l'ampli** : IP LAN du serveur (pas
  127.0.0.1), pare-feu ouvert sur le port de push (9000 par défaut) ;
- **un seul serveur de push à la fois** sur un port donné : si une vieille
  instance survit, l'ampli peut tirer le fichier depuis un serveur qui ne sert
  plus rien → `ERROR_OCCURRED` ;
- faute SOAP `<s:Fault>` = URI injouable (format non supporté, serveur
  injoignable…) — toujours lire le `errorDescription`.

**États du transport** : `NO_MEDIA_PRESENT` (rien de chargé) → `TRANSITIONING`
(après SetURI/Play) → `PLAYING` → `PAUSED_PLAYBACK` → `STOPPED`. Fin de piste =
`STOPPED`.

---

## 4. Formats audio

Sink déclaré par `GetProtocolInfo` :

```
audio/L16;rate=44100;channels=1|2   (LPCM)     audio/mpeg       (MP3)
audio/L16;rate=48000;channels=1|2   (LPCM)     audio/x-ms-wma   (WMA)
http-wavetunes:*:audio/x-ms-wma     (WMA streaming)
```

Le décodeur du R-N301 (entrée SERVER) supporte plus que ce que le renderer
déclare. État des validations live :

| Format | Push direct |
|---|---|
| WAV / LPCM 44.1 kHz stéréo | ✅ |
| MP3 | ✅ (durée piste correcte) |
| FLAC | ✅ |
| MP4 / M4A (AAC) | ✅ |
| WMA | non testé |
| Webradio (flux HTTP direct via `push_url`) | non testé |
| OGG / Opus | non testé (mime servi par la lib, décodage ampli inconnu) |

Format non accepté → transcoder à la volée en WAV/LPCM ou MP3 (ffmpeg) :
ce sont les formats les plus sûrs du renderer. LPCM = aussi le format idéal
pour tout flux généré (TTS, mix…) : durée connue → `Content-Length` exact.

---

## 5. Eventing GENA

Les trois services déclarent un `eventSubURL` et **le renderer honore GENA**
(validé en live) :

```http
SUBSCRIBE /AVTransport/evt HTTP/1.1        → 200
  CALLBACK: <http://<ip-client>:<port>/cb>    SID: uuid:…
  NT: upnp:event                              TIMEOUT: Second-300   ← plafond
  TIMEOUT: Second-300                         (Second-600 demandé → 300 rendu)

NOTIFY (initial immédiat, puis à chaque changement)
  NTS: upnp:propchange   SEQ: 0,1,2…
  <e:propertyset><e:property><LastChange>&lt;Event…&gt;…

UNSUBSCRIBE + SID                          → 200
```

### Portée réelle (mesurée sur 420 s avec manipulations physiques)

| GENA notifie ✅ | GENA ne voit jamais ❌ |
|---|---|
| `Volume[Master]` — bouton de façade, télécommande (~30 ms plus vite que le polling YNC) | `Power` On ↔ Standby |
| `Mute[Master]` | `Input_Sel` (changements de source) |
| `TransportState` du **push DLNA** uniquement | lecture réseau SERVER / `play_info` YNC |
| | tuner, navigation des listes |

**GENA complète le polling YNC, il ne le remplace pas.** Seule la partie
volume/mute + transport du push est événementielle ; power, source, play_info,
listes et tuner restent à interroger en YNC.

### Comportements à connaître (mesurés)

1. **`LastChange` est la seule variable `sendEvents="yes"`** : un document XML
   *échappé* dans une balise XML (double décodage) ; les variables
   individuelles ne sont pas notifiées séparément.
2. **Incrémental** : seul `SEQ=0` porte l'état complet (`PresetNameList` +
   `Mute` + `Volume`) ; les NOTIFY suivants ne contiennent que la variable
   modifiée → il faut **accumuler** un état local (deltas), jamais écraser.
3. **Modéré (coalescé)** : l'ampli regroupe les notifications — en tournant
   vite le bouton de volume, GENA envoie moins d'events que de pas réels et
   saute des valeurs intermédiaires. La **valeur finale est fiable**,
   l'historique non.
4. **Attributs inversés** : `<Volume channel="Master" val="50"/>` — `channel`
   avant `val` (piège pour les regex naïves ; parsing ElementTree obligatoire).
5. **TIMEOUT plafonné à 300 s** par le firmware → renouvellement du SID à
   caler vers 240 s (SUBSCRIBE avec `SID` au lieu de `NT`/`CALLBACK`).
6. **Le callback doit répondre `200`** aux NOTIFY, sinon l'ampli déclare
   l'abonné mort. Il doit être joignable depuis l'ampli (IP LAN, pare-feu).
7. **Trous de `SEQ`** = events perdus → se re-synchroniser en SOAP
   (`GetTransportInfo`…) ; `GenaClient.missed()` les compte.
8. **Un seul abonné actif par service, en pratique.** Constaté en live : un
   processus tué sans `UNSUBSCRIBE` laisse un SID fantôme qui **affame** les
   nouveaux abonnés (SUBSCRIBE → 200 + SID, mais aucun NOTIFY livré, pas même
   l'initial) jusqu'à son expiration (~300 s sans renouvellement). Règle :
   une seule instance de client GENA par ampli, et toujours fermer proprement
   (`close()` / Ctrl+C → UNSUBSCRIBE). Si les events s'arrêtent : attendre
   ~5 min ou redémarrer l'ampli.

### Échelle de volume : UPnP == YNC

Mesuré au plafond : GENA `Volume[Master]` = **80** et YNC
`Basic_Status/Val` = **80** au même instant. Les deux échelles sont
identiques, aucun mapping à écrire. Le `allowedValueRange 0..100` du SCPD
RenderingControl est **déclaratif et non appliqué** : le plafond réel vient du
réglage « Max Volume » de l'ampli (façade), qui n'expose aucun chemin YNC de
lecture. La lib travaille donc sur l'échelle protocole 0..100 et laisse le
firmware clampler (+ `RN301(max_volume=N)` optionnel, plafond logiciel).

Le contrôle du volume reste fait en **YNC** par la lib : le canal
`SetVolume`/`SetMute` du RenderingControl existe (mêmes valeurs) mais n'est
pas exploité — une seule voie d'écriture, deux voies de lecture (YNC + GENA).

---

## 6. API de la lib

### `PushServer` (`rn301/push.py`) — façade

```python
push = PushServer(amp, serve_host="0.0.0.0", serve_port=9000, renderer_port=8080)

push.push_file("morceau.flac", title="", artist="", album="", play=True)
    # sert le fichier (FileServer) + extrait tags/pochette intégrés (ID3v2,
    # FLAC Vorbis/PICTURE, MP4 atoms) + SetAVTransportURI (DIDL complet) + Play
push.push_url("http://nas/radio.mp3", title="", mime="audio/mpeg", play=True)
    # URL déjà servie sur le LAN : pas de serveur local

push.play(); push.pause(); push.stop()      # transport
push.state()        # {'state', 'status', 'uri', 'rel_time', 'track_duration'}
push.last_meta      # {'title', 'artist', 'album', 'cover'} du dernier push

# --- événements (GENA) — optionnels, paresseux, passifs
push.on_transport(lambda state, changed: ...)  # callback à chaque NOTIFY
push.wait_stopped(timeout=None, arm_timeout=10.0)
    # bloque jusqu'à la fin de la piste, en 2 phases : attend d'abord PLAYING
    # (anti-course avec un vieux STOPPED) puis STOPPED/NO_MEDIA_PRESENT
push.transport_state()                         # état courant (cache, 0 requête)
push.close()                                   # UNSUBSCRIBE + stop serveur
```

La lib ne gère **aucune file d'attente** : chaque `push_file()` est
indépendant, c'est le client qui décide quoi pousser ensuite (une boucle
`for f in files: push_file(f); wait_stopped()` suffit).

### `GenaClient` (`rn301/gena.py`) — abonné GENA générique

```python
gena = GenaClient(amp)
gena.subscribe("RenderingControl", lambda svc, changed: ...)  # + AVTransport
gena.state("RenderingControl")   # état accumulé {'Volume': {'channel','val'}, …}
gena.var("RenderingControl", "Volume")
gena.missed("AVTransport")       # events perdus (trous de SEQ)
gena.wait_for(service, predicate, timeout)    # bloque jusqu'à condition
gena.close()                     # UNSUBSCRIBE propre (aussi via `with`)
```

Serveur de callback intégré (répond 200), renouvellement automatique des SID
(~240 s, re-SUBSCRIBE complet sur 404/412), thread-safe. Self-test live :
`python -m rn301.gena [durée] [ip]`.

### Briques internes

- `Renderer` : client SOAP AVTransport (`set_uri`, `play`, `pause`, `stop`,
  `transport_info`, `position_info`, `seek`* — *refusé par le firmware)
- `FileServer` : serveur HTTP mono-fichier (Range/206, HEAD, mime par
  extension, `/albumart`)
- `didl_metadata()` : constructeur DIDL-Lite (OP par format)
- `extract_tags()` / `extract_cover()` : lecture des métadonnées et pochettes
  intégrées (ID3v2 APIC, FLAC Vorbis/PICTURE, MP4 atoms) — stdlib only

YNC et SOAP partagent un **verrou par ampli** (`rn301/_locks.py`) : le firmware
ne traite qu'une requête à la fois, la sérialisation est automatique entre
tous les clients `RN301`/`Renderer` d'une même IP.

---

## 7. L'app officielle, pour référence

Le push de la lib est une réplique du module `com.yamaha.av.localfilecontroller`
de NP Controller (22 classes décompilées) : le téléphone ouvre un
`ServerSocket(0)`, sert ses fichiers MediaStore en HTTP (routes `GET /audio/<id>/`
et `/albumart/<id>`, Range/206, `TransferMode.DLNA.ORG: Streaming`), et un point
de contrôle envoie `SetAVTransportURI` (DIDL avec `DLNA.ORG_OP` +
`DLNA.ORG_FLAGS=017…`) puis `Play`. Rien de propriétaire : du DLNA standard —
toute URL audio servie en HTTP sur le LAN peut être envoyée à l'ampli.

Ce que ça débloque : jouer n'importe quel fichier du PC/téléphone sans NAS,
des flux générés à la volée (ffmpeg : transcode, TTS, mix), des webradios hors
vTuner (URL directe), un contrôle depuis plusieurs clients.

---

## Outils de reproduction

| Outil | Rôle |
|---|---|
| `tools/probe_gena.py` | sonde **read-only** : dump SCPD AVTransport, capacités live, cycle SUBSCRIBE/NOTIFY/UNSUBSCRIBE |
| `tools/probe_gena_raw.py` | mesure de couplage : body brut horodaté des NOTIFY + polling YNC parallèle (c'est lui qui a produit les tableaux du §5) |
| `tools/test_push.py` | push standalone d'un fichier (serveur + SetAVTransportURI + Play) |
| `python -m rn301.discover` | scan SSDP → identité complète de l'ampli |
| `python -m rn301.gena 60` | écoute live des events GENA |

Archives brutes : `amp/upnp_desc.xml`, `amp/upnp_avtransport.xml`,
`amp/upnp_rendering.xml`, `amp/upnp_connectionmanager.xml`.
