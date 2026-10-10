"""Serveur web de contrôle pour le Yamaha R-N301.

100 % stdlib. API JSON + page de contrôle intégrée.
Volume/mute en push instantané via GENA → SSE (`/api/events`) ; le reste de
l'état est pollé toutes les 4 s (GENA ne notifie ni Power ni Input_Sel).

Usage:
    .venv/Scripts/python -m webapp.server [--amp 192.168.1.91] [--port 8080]
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import threading
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rn301 import RN301, YNCError, GenaClient
from rn301.push import PushServer, PushError

amp: RN301      # initialisé dans main()
push: PushServer | None = None
gena: GenaClient | None = None
music_dir: str = "music"


class EventHub:
    """Hub SSE : les callbacks GENA publient, chaque navigateur connecté reçoit."""

    def __init__(self):
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, data: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass                       # client trop lent : tant pis, le polling rattrape

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


hub = EventHub()


def on_gena_event(service: str, changed: dict) -> None:
    """Callback GENA (thread du serveur de callback) → hub SSE.

    RenderingControl notifie Volume/Mute (échelle identique au YNC, mesuré) ;
    attributs dans l'ordre channel-avant-val déjà gérés par GenaClient.
    """
    out: dict = {}
    v = changed.get("Volume")
    if v and v.get("channel") == "Master" and v.get("val", "").isdigit():
        out["volume"] = int(v["val"])
    m = changed.get("Mute")
    if m and m.get("channel") == "Master":
        out["mute"] = "On" if m.get("val") == "1" else "Off"
    if out:
        hub.publish(out)

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yamaha R-N301</title>
<style>
:root{--bg:#101418;--card:#1a2028;--acc:#4fc3f7;--txt:#e8eef2;--dim:#8a99a8;--red:#ef5350;--grn:#66bb6a}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--txt);font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px}
h1{font-size:1.2rem;color:var(--acc);margin-bottom:4px}
.sub{color:var(--dim);font-size:.8rem;margin-bottom:16px}
.card{background:var(--card);border-radius:12px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{background:#263040;color:var(--txt);border:1px solid #35445a;border-radius:8px;padding:10px 14px;font-size:.95rem;cursor:pointer;transition:.15s}
button:hover{border-color:var(--acc);color:var(--acc)}
button:active{transform:scale(.96)}
button.on{background:var(--acc);color:#08111a;border-color:var(--acc);font-weight:600}
button.pwr.on{background:var(--grn);border-color:var(--grn)}
button.big{font-size:1.3rem;padding:12px 20px}
input[type=range]{flex:1;accent-color:var(--acc);height:28px}
.volval{min-width:52px;text-align:center;font-variant-numeric:tabular-nums;font-weight:600}
.src{flex:1 1 30%;text-align:center}
.now{display:flex;gap:12px;align-items:center}
.now img{width:72px;height:72px;border-radius:8px;background:#000;object-fit:cover}
.now .t{font-weight:600}
.now .s{color:var(--dim);font-size:.85rem}
.list{font-size:.9rem}
.list .li{padding:7px 10px;border-radius:6px;display:flex;gap:8px;cursor:pointer}
.list .li:hover{background:#263040}
.list .li.sel{background:#2a3b50;color:var(--acc)}
.list .li.un{color:var(--dim);cursor:default}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;background:#263040;color:var(--dim);font-size:.72rem}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--red);color:#fff;padding:8px 16px;border-radius:8px;font-size:.85rem;opacity:0;pointer-events:none;transition:.3s}
#toast.show{opacity:1}
.lbl{color:var(--dim);font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
.gap{height:8px}
details summary{cursor:pointer;color:var(--dim);font-size:.85rem}
</style>
</head>
<body>
<h1>🎛️ Yamaha R-N301</h1>
<div class="sub" id="sysinfo">connexion…</div>

<div class="card">
  <div class="row">
    <button class="big pwr" id="btnPower" onclick="api('power',{},true)">⏻</button>
    <div style="flex:1"></div>
    <button id="btnMute" onclick="api('mute',{},true)">🔇 Mute</button>
  </div>
  <div class="gap"></div>
  <div class="row">
    <button onclick="vol(-2)">−</button>
    <input type="range" id="vol" min="0" max="100" step="1" onchange="api('volume',{val:this.value})">
    <button onclick="vol(+2)">＋</button>
    <span class="volval" id="volval">--</span>
  </div>
</div>

<div class="card">
  <div class="lbl">Source</div>
  <div class="row" id="sources"></div>
</div>

<div class="card" id="nowcard" style="display:none">
  <div class="lbl">Lecture <span class="badge" id="playstate"></span></div>
  <div class="now">
    <img id="art" style="display:none" alt="">
    <div>
      <div class="t" id="npTitle"></div>
      <div class="s" id="npSub"></div>
    </div>
  </div>
  <div class="gap"></div>
  <div class="row">
    <button id="btnPrev" onclick="api('playback',{action:'Skip Rev'})">⏮</button>
    <button class="big" onclick="playBtn()" id="btnPlay">▶</button>
    <button id="btnNext" onclick="api('playback',{action:'Skip Fwd'})">⏭</button>
    <button onclick="pushMode?api('pushctl',{action:'Stop'}):api('playback',{action:'Stop'})">⏹</button>
    <div style="flex:1"></div>
    <button id="btnShuffle" onclick="api('playmode',{what:'shuffle',value:shuffleNext()})">🔀</button>
    <button id="btnRepeat" onclick="api('playmode',{what:'repeat',value:repeatNext()})">🔁</button>
  </div>
</div>

<div class="card" id="tunercard" style="display:none">
  <div class="lbl">Tuner <span class="badge" id="tunerinfo"></span></div>
  <div class="row">
    <button onclick="api('tuner',{op:'step',band:band,dir:'Down'})">◀</button>
    <select id="bandsel" onchange="band=this.value;refresh()"><option>FM</option><option>AM</option></select>
    <button onclick="api('tuner',{op:'step',band:band,dir:'Up'})">▶</button>
    <button onclick="api('tuner',{op:'search',band:band,dir:'Up'})">🔍 Auto</button>
    <input type="number" id="freq" step="0.1" style="width:90px;background:#0d1117;color:var(--txt);border:1px solid #35445a;border-radius:6px;padding:8px">
    <button onclick="api('tuner',{op:'tune',band:band,freq:document.getElementById('freq').value})">Go</button>
  </div>
  <div class="gap"></div>
  <div class="row" id="presets"></div>
</div>

<div class="card" id="listcard" style="display:none">
  <div class="lbl">Navigateur <span class="badge" id="menuname"></span></div>
  <div class="list" id="menu"></div>
  <div class="gap"></div>
  <div class="row">
    <button onclick="api('cursor',{dir:'Return'})">↩ Retour</button>
    <button onclick="api('cursor',{dir:'Up'})">▲</button>
    <button onclick="api('cursor',{dir:'Down'})">▼</button>
    <button onclick="api('cursor',{dir:'Sel'})">OK</button>
  </div>
</div>

<div class="card" id="pushcard" style="display:none">
  <div class="lbl">📲 Mode Push — audio du navigateur <span class="badge" id="pushstate"></span></div>
  <div class="row">
    <input type="file" id="pushfile" accept="audio/*" style="flex:1;color:var(--dim);font-size:.85rem">
    <button onclick="pushFile()">Pousser 🔊</button>
  </div>
  <div class="gap"></div>
  <div class="row">
    <input type="url" id="pushurl" placeholder="http://… (webradio MP3, NAS…)" style="flex:1;background:#0d1117;color:var(--txt);border:1px solid #35445a;border-radius:6px;padding:8px;font-size:.85rem">
    <button onclick="api('push',{url:document.getElementById('pushurl').value})">Go</button>
  </div>
  <div class="gap"></div>
  <div class="lbl" style="margin:0">Transport (▶ ⏸ ⏹) dans la carte Lecture ci-dessus</div>
</div>

<div class="card">
  <details>
    <summary>⚙️ Système</summary>
    <div class="gap"></div>
    <div class="row">
      <button id="btnNetStdby" onclick="api('netstandby',{},true)">Network Standby</button>
      <span class="badge" id="netname"></span>
    </div>
    <div class="gap"></div>
    <div class="row">
      <span class="lbl" style="margin:0">Touche IR</span>
      <button onclick="api('remote',{key:'net_cycle'})">Cycle réseau</button>
      <button onclick="api('remote',{key:'shuffle'})">Shuffle IR</button>
      <button onclick="api('remote',{key:'repeat'})">Repeat IR</button>
    </div>
    <div class="gap"></div>
    <div class="lbl" style="margin:0">Sleep / Gapless / Device_Name : non supportés par ce firmware en YNC</div>
  </details>
</div>

<div id="toast"></div>

<script>
let state={}, band='FM', shuffle=false, repeat='Off', playing=false, pushMode=false;

function enterPush(){pushMode=true;api('input',{val:'SERVER'});}
function exitPush(v){pushMode=false;api('input',{val:v});}

function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500);}
async function api(action,body,norefresh){
  try{
    const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||r.status);
    if(j.state) render(j.state);
  }catch(e){toast('⚠ '+e.message);}
  if(!norefresh) refresh();
}
function vol(d){const v=Math.max(0,Math.min(100,(state.volume||0)+d));api('volume',{val:v});}
function togglePlay(){return playing?'Pause':'Play';}
function playBtn(){pushMode?api('pushctl',{action:playing?'Pause':'Play'}):api('playback',{action:togglePlay()});}
function shuffleNext(){shuffle=!shuffle;return shuffle?'On':'Off';}
function repeatNext(){repeat=repeat==='Off'?'All':repeat==='All'?'One':'Off';return repeat;}

async function pushFile(){
  const inp=document.getElementById('pushfile');
  const f=inp.files&&inp.files[0];
  if(!f){toast('choisis un fichier audio');return;}
  toast('upload…');
  try{
    const r=await fetch('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f});
    const j=await r.json();
    if(!r.ok||j.error)throw new Error(j.error||r.status);
    await api('push',{file:j.path});   // titre/artiste/album lus depuis les tags
    inp.value='';
  }catch(e){toast('⚠ '+e.message);}
}

async function refresh(){
  try{
    const r=await fetch('/api/state'); const j=await r.json();
    if(j.state) render(j.state);
  }catch(e){document.getElementById('sysinfo').textContent='ampli injoignable';}
}

function render(s){
  state=s;
  const on=s.power==='On';
  document.getElementById('sysinfo').textContent=`${s.model||'R-N301'} · v${s.version||'?'} · ${on?'🟢 allumé':'⚫ veille'}${genaLive?' · ⚡GENA':''}`;
  const bp=document.getElementById('btnPower'); bp.classList.toggle('on',on);
  document.getElementById('btnMute').classList.toggle('on',s.mute==='On');
  document.getElementById('vol').value=s.volume??0;
  document.getElementById('volval').textContent=on?(s.volume??'--'):'--';

  // sources (+ pseudo-source PUSH en dernier, comme le mode "Device" de l'app)
  const ps=s.push_state||{};
  if(['PLAYING','TRANSITIONING','PAUSED_PLAYBACK'].includes(ps.state)) pushMode=true;
  if(s.input!=='SERVER') pushMode=false;
  const sc=document.getElementById('sources');
  if(!sc.children.length && s.inputs){
    s.inputs.forEach(i=>{
      const b=document.createElement('button'); b.className='src'; b.textContent=i.value;
      b.onclick=()=>{pushMode=false;api('input',{val:i.value});}; sc.appendChild(b);
    });
    const bp=document.createElement('button'); bp.className='src'; bp.id='srcPush';
    bp.textContent='📱 PUSH'; bp.style.borderColor='var(--acc)';
    bp.onclick=()=>enterPush(); sc.appendChild(bp);
  }
  [...sc.children].forEach(b=>{
    if(b.id==='srcPush') b.classList.toggle('on',pushMode);
    else b.classList.toggle('on',!pushMode&&b.textContent===s.input);
  });

  // now playing (sources réseau)
  const inp=(s.input||'').toUpperCase();
  const net=['NET RADIO','SERVER','SPOTIFY','AIRPLAY'].includes(inp);
  const pi=s.play_info||{};
  const nc=document.getElementById('nowcard');
  if(on&&net&&pi.available!=='Not Ready'){
    nc.style.display='';
    playing=pushMode?['PLAYING','TRANSITIONING'].includes(ps.state):(pi.playback||'')==='Play';
    document.getElementById('btnPlay').textContent=playing?'⏸':'▶';
    const m=pi.meta||{};
    document.getElementById('npTitle').textContent=m.song||m.station||'—';
    document.getElementById('npSub').textContent=[m.artist,m.album].filter(Boolean).join(' · ');
    document.getElementById('playstate').textContent=pi.playback||'';
    const art=document.getElementById('art');
    if(pi.album_art){art.src='/art?url='+encodeURIComponent(pi.album_art);art.style.display='';}
    else art.style.display='none';
    if(pi.shuffle!==undefined){shuffle=pi.shuffle==='On';document.getElementById('btnShuffle').classList.toggle('on',shuffle);}
    if(pi.repeat!==undefined){repeat=pi.repeat;document.getElementById('btnRepeat').classList.toggle('on',repeat!=='Off');document.getElementById('btnRepeat').textContent=repeat==='One'?'🔂':'🔁';}
  } else nc.style.display='none';

  // tuner
  const tc=document.getElementById('tunercard');
  if(on&&inp==='TUNER'){
    tc.style.display='';
    const f=pi.freq, u=pi.freq_unit;
    document.getElementById('tunerinfo').textContent=
      `${pi.band||'?'} ${f?(u==='MHz'?(f/100).toFixed(2)+' MHz':f+' kHz'):''} ${pi.tuned==='Positive'?'✓':''} ${pi.stereo==='Positive'?'stereo':''}`;
    band=pi.band||'FM'; document.getElementById('bandsel').value=band;
    document.getElementById('freq').value=band==='FM'?(f/100).toFixed(2):f;
    const ps=document.getElementById('presets');
    if(!ps.children.length){for(let n=1;n<=8;n++){const b=document.createElement('button');b.textContent=n;b.onclick=()=>api('tuner',{op:'preset',n});ps.appendChild(b);}}
  } else tc.style.display='none';

  // navigateur liste SERVER / NET RADIO
  const lc=document.getElementById('listcard');
  const li=s.list_info;
  if(on&&!pushMode&&li&&li.menu_status==='Ready'&&li.lines&&li.lines.length){
    lc.style.display='';
    document.getElementById('menuname').textContent=li.menu_name||('layer '+li.menu_layer);
    const menu=document.getElementById('menu'); menu.innerHTML='';
    const cur=parseInt(li.cursor_line||'0');
    li.lines.forEach(l=>{
      if(!l.txt&&l.attribute==='Unselectable')return;
      const d=document.createElement('div');
      d.className='li'+(l.line===cur?' sel':'')+(l.attribute==='Unselectable'?' un':'');
      d.textContent=(l.line===cur?'▸ ':'')+l.txt;
      if(l.attribute!=='Unselectable')d.onclick=()=>api('line',{n:l.line});
      menu.appendChild(d);
    });
  } else lc.style.display='none';

  // push (carte visible seulement en mode push)
  document.getElementById('pushcard').style.display=(on&&pushMode)?'':'none';
  document.getElementById('pushstate').textContent=ps.state||'—';

  // système
  document.getElementById('btnNetStdby').classList.toggle('on',s.net_standby==='On');
  if(s.net_name)document.getElementById('netname').textContent=s.net_name;
}

refresh(); setInterval(refresh,4000);

// volume/mute en push instantané (GENA → SSE) : le slider suit la télécommande
// physique et le bouton de façade en ~30 ms, sans attendre le polling 4 s.
let genaLive=false;
const es=new EventSource('/api/events');
es.onopen=()=>{genaLive=true;};
es.onerror=()=>{genaLive=false;};
es.onmessage=e=>{
  try{
    const d=JSON.parse(e.data);
    genaLive=true;
    if(d.volume!==undefined){
      state.volume=d.volume;
      document.getElementById('vol').value=d.volume;
      document.getElementById('volval').textContent=(state.power==='On')?d.volume:'--';
    }
    if(d.mute!==undefined){
      state.mute=d.mute;
      document.getElementById('btnMute').classList.toggle('on',d.mute==='On');
    }
  }catch(_){}
};
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "RN301Web/1.0"
    protocol_version = "HTTP/1.1"      # keep-alive : nécessaire au flux SSE

    def log_message(self, fmt, *args):
        pass  # silencieux

    # ---------------------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def full_state(self) -> dict:
        """Agrège l'état ampli pour l'UI (plusieurs GET, un par chemin)."""
        try:
            st = amp.basic_status()
        except YNCError as e:
            return {"error": str(e)}
        out = dict(st)
        try:
            cfg = amp.system_config()
            out["model"], out["version"] = cfg["model"], cfg["version"]
        except YNCError:
            pass
        # inputs (cache : la liste ne change pas)
        if not getattr(amp, "_inputs_cache", None):
            try:
                amp._inputs_cache = amp.list_inputs()
            except YNCError:
                amp._inputs_cache = []
        out["inputs"] = amp._inputs_cache
        # infos source réseau / tuner (Input_Sel renvoie 'NET RADIO', 'TUNER'... en majuscules)
        net_sources = ("NET RADIO", "SERVER", "SPOTIFY", "AIRPLAY", "TUNER")
        if (out.get("input") or "").upper() in net_sources:
            try:
                out["play_info"] = amp.play_info(out["input"])
            except YNCError:
                out["play_info"] = None
            if out["input"].upper() in ("SERVER", "NET RADIO"):
                try:
                    out["list_info"] = amp.list_info(out["input"])
                except YNCError:
                    out["list_info"] = None
        # réglages système (Network_Standby/Name sous System/Misc/Network sur ce firmware)
        for key, fn in (("net_standby", amp.get_network_standby),
                        ("net_name", amp.get_network_name)):
            try:
                out[key] = fn()
            except YNCError:
                out[key] = None
        # état du renderer DLNA (push)
        if push is not None:
            try:
                out["push_state"] = push.state()
                out["push_meta"] = push.last_meta
            except PushError:
                out["push_state"] = None
        return out

    # ---------------------------------------------------------------- routes

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json({"state": self.full_state()})
        elif path == "/api/events":
            self.sse_stream()
        elif path == "/api/inputs":
            try:
                self._json({"inputs": amp.list_inputs()})
            except YNCError as e:
                self._json({"error": str(e)}, 502)
        elif path == "/api/config":
            try:
                self._json(amp.system_config())
            except YNCError as e:
                self._json({"error": str(e)}, 502)
        elif path == "/pushart":
            # pochette intégrée du fichier en cours de push
            if push is not None and push.server.cover_data:
                self._send(200, push.server.cover_data, push.server.cover_mime)
            else:
                self._json({"error": "pas de pochette"}, 404)
        elif path == "/art":
            # proxy pochette d'album (l'URL est relative à l'ampli)
            from urllib.parse import urlparse as _up, parse_qs as _pq
            url = (_pq(_up(self.path).query).get("url") or [""])[0]
            if not url:
                self._json({"error": "url requise"}, 400)
                return
            if url.startswith("/"):
                url = f"http://{amp.url.split('/')[2]}{url}"
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "image/jpeg")
                self._send(200, data, ctype)
            except OSError:
                self._json({"error": "image injoignable"}, 502)
        else:
            self._json({"error": "not found"}, 404)

    def sse_stream(self):
        """Flux Server-Sent Events : volume/mute poussés par GENA en direct.

        Un thread par client (ThreadingHTTPServer) ; le flux reste ouvert,
        commentaires `:ping` toutes les 15 s pour maintenir la connexion.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = hub.register()
        try:
            # état courant immédiat (le client n'attend pas le premier changement)
            if gena is not None:
                seed = {}
                v = gena.var("RenderingControl", "Volume")
                if v and v.get("val", "").isdigit():
                    seed["volume"] = int(v["val"])
                m = gena.var("RenderingControl", "Mute")
                if m:
                    seed["mute"] = "On" if m.get("val") == "1" else "Off"
                if seed:
                    self.wfile.write(f"data: {json.dumps(seed)}\n\n".encode())
                    self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b":ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
        except (OSError, ValueError):
            pass                              # client parti : BrokenPipe/ConnectionReset
        finally:
            hub.unregister(q)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/upload":
            self.handle_upload()
            return
        body = self._body()
        try:
            result = self.dispatch(path, body)
        except YNCError as e:
            self._json({"error": f"ampli: {e}"}, 502)
            return
        except PushError as e:
            self._json({"error": f"push: {e}"}, 502)
            return
        except (ValueError, KeyError, TypeError) as e:
            self._json({"error": f"paramètre invalide: {e}"}, 400)
            return
        except Exception:
            traceback.print_exc()
            self._json({"error": "erreur interne"}, 500)
            return
        if isinstance(result, dict):
            self._json(result)
        else:
            self._json({"ok": True, "state": self.full_state()})

    def handle_upload(self):
        """Reçoit un fichier audio brut (body binaire) dans music_dir/ pour push."""
        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("name") or ["upload.bin"])[0]
        name = re.sub(r"[^\w.\- ]", "_", os.path.basename(name))[:120] or "upload.bin"
        os.makedirs(music_dir, exist_ok=True)
        dest = os.path.join(music_dir, name)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            self._json({"error": "corps vide"}, 400)
            return
        with open(dest, "wb") as f:
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 16, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        self._json({"ok": True, "path": os.path.abspath(dest), "name": name})

    def dispatch(self, path: str, body: dict):
        """Une route = une commande documentée. Retourne dict brut ou None (+state)."""
        match path:
            # --- zone principale
            case "/api/power":
                if "on" in body:
                    amp.set_power(bool(body["on"]))
                else:
                    return {"ok": True, "power": amp.toggle_power(), "state": self.full_state()}
            case "/api/volume":
                if "val" in body:
                    amp.set_volume(int(body["val"]))
                elif "delta" in body:
                    d = int(body["delta"])
                    amp.volume_up(d) if d > 0 else amp.volume_down(-d)
                else:
                    raise ValueError("volume: 'val' ou 'delta' requis")
            case "/api/mute":
                if "on" in body:
                    amp.set_mute(bool(body["on"]))
                else:
                    return {"ok": True, "mute": amp.toggle_mute(), "state": self.full_state()}
            case "/api/input":
                amp.set_input(body["val"])
            # --- lecture
            case "/api/playback":
                amp.playback(body.get("source", amp.get_input()), body.get("action", "Play"))
            case "/api/playmode":
                src = body.get("source", amp.get_input())
                if body.get("what") == "shuffle":
                    amp.set_shuffle(src, str(body.get("value", "On")).lower() == "on")
                else:
                    amp.set_repeat(src, body.get("value", "Off"))
            case "/api/list":
                return {"ok": True, "list": amp.list_info(body.get("source", amp.get_input()))}
            case "/api/cursor":
                amp.cursor(body.get("source", amp.get_input()), body.get("dir", "Down"))
            case "/api/page":
                amp.page(body.get("source", amp.get_input()), body.get("dir", "Down"))
            case "/api/line":
                amp.select_line(body.get("source", amp.get_input()), int(body["n"]))
            case "/api/jump":
                # non supporté par le firmware (HTTP 400) — conservé pour mémoire
                amp.jump_line(body.get("source", amp.get_input()), int(body["line"]))
            # --- tuner
            case "/api/tuner":
                op = body.get("op", "tune")
                if op == "tune":
                    amp.tune(body.get("band", "FM"), body["freq"])
                elif op == "band":
                    amp.set_band(body.get("band", "FM"))
                elif op == "step":
                    amp.tuning_step(body.get("dir", "Up"))
                elif op == "preset":
                    amp.select_preset(int(body["n"]))
                elif op == "preset_step":
                    amp.select_preset(body.get("dir", "Up"))
                elif op == "search":
                    amp.auto_search(body.get("band", "FM"), body.get("dir", "Up"))
                else:
                    raise ValueError(f"op tuner inconnue: {op}")
            # --- système (sleep/gapless/device_name : non supportés par ce firmware)
            case "/api/netstandby":
                cur = amp.get_network_standby()
                on = bool(body["on"]) if "on" in body else cur != "On"
                amp.set_network_standby(on)
                return {"ok": True, "net_standby": "On" if on else "Off", "state": self.full_state()}
            case "/api/netname":
                amp.set_network_name(body["name"])
            case "/api/macfilter":
                amp.set_mac_filter(body.get("mode", "Off"),
                                   *[body.get(f"a{i}") for i in range(1, 7)])
            case "/api/update":
                amp.firmware_update_start()
            case "/api/systempower":
                amp.system_power(bool(body.get("on", True)))
            # --- IR virtuel
            case "/api/remote":
                if "code" in body:
                    amp.remote_signal(body["code"])
                else:
                    amp.remote_key(body["key"])
            # --- push DLNA direct (MediaRenderer :8080)
            case "/api/push":
                if push is None:
                    return {"error": "push désactivé (--no-push)"}
                # le renderer ne joue que sur l'entrée réseau SERVER :
                # basculer dessus si besoin (sinon PLAYING mais muet !)
                import time
                try:
                    cur = amp.get_input()
                except YNCError:
                    cur = None
                if cur != "SERVER":
                    amp.set_input("SERVER")
                    time.sleep(4)   # RC=3 "occupé" après changement de source réseau
                kw = {k: body[k] for k in ("title", "artist", "album") if body.get(k)}
                if body.get("file"):
                    r = push.push_file(body["file"], **kw)
                elif body.get("url"):
                    r = push.push_url(body["url"], mime=body.get("mime", "audio/mpeg"), **kw)
                else:
                    raise ValueError("file ou url requis")
                return {"ok": True, "push": r, "state": self.full_state()}
            case "/api/pushctl":
                if push is None:
                    return {"error": "push désactivé (--no-push)"}
                action = body.get("action", "Play")
                if action == "Play":
                    push.play()
                elif action == "Pause":
                    push.pause()
                elif action == "Stop":
                    push.stop()
                elif action == "Seek":
                    push.seek(body.get("target", "0:00:00"))
                else:
                    raise ValueError(f"action inconnue: {action}")
                return {"ok": True, "push": push.state(), "state": self.full_state()}
            # --- passe-plat YNC brut (pour explorer)
            case "/api/raw":
                root = amp._request(body.get("cmd", "GET").upper(), body["xml"])
                import xml.etree.ElementTree as ET
                return {"ok": True, "rsp": ET.tostring(root, encoding="unicode")}
            case _:
                return {"error": "not found"}
        return None


def main():
    p = argparse.ArgumentParser(description="Serveur web Yamaha R-N301")
    p.add_argument("--amp", default="192.168.1.91", help="IP de l'ampli")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--push-port", type=int, default=9000, help="port HTTP servant les fichiers poussés")
    p.add_argument("--music", default="music", help="dossier des uploads")
    p.add_argument("--no-push", action="store_true", help="désactive le push DLNA")
    p.add_argument("--no-gena", action="store_true",
                   help="désactive les events GENA (volume/mute en push SSE)")
    args = p.parse_args()

    global amp, push, gena, music_dir
    amp = RN301(args.amp)
    music_dir = args.music
    if not args.no_push:
        push = PushServer(args.amp, serve_port=args.push_port)
        push.start()
        print(f"push DLNA: fichiers servis sur :{args.push_port} (uploads -> {music_dir}/)")
    if not args.no_gena:
        # Volume/mute instantanés : GENA RenderingControl -> hub -> SSE /api/events.
        # Dégradé gracieux : si l'abonnement échoue, le polling 4 s suffit.
        try:
            gena = GenaClient(args.amp,
                              on_error=lambda svc, msg: print(f"GENA {svc}: {msg}"))
            gena.subscribe("RenderingControl", on_gena_event)
            print("GENA: volume/mute en push (RenderingControl abonné)")
        except Exception as e:
            gena = None
            print(f"GENA indisponible ({e}) — polling 4 s uniquement")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"R-N301 web: http://localhost:{args.port}  (ampli: {args.amp})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        if gena is not None:
            gena.close()
        if push is not None:
            push.close()


if __name__ == "__main__":
    main()
