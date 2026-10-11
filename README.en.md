# rn301 — Full control of a Yamaha R-N301 (Python + web)

[Français](README.md) · English

[![CI](https://github.com/gdelaunay/rn301/actions/workflows/ci.yml/badge.svg)](https://github.com/gdelaunay/rn301/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.12%20|%203.13-blue)](#quick-start)

Python library and web server to control a **Yamaha R-N301** (2013) hi-fi
amplifier without the official app. The **YNC** (Yamaha Network
Control) protocol was reverse-engineered from the *NP Controller 4.60* APK and
validated command-by-command, live, on firmware 1.13/0.05.

**100 % stdlib** — zero dependencies.

The R-N301 publishes **no YNC descriptor** (`desc.xml` → 404): generic YNC
libraries, which discover commands through that file, cannot work with it. The
~90 commands were therefore extracted directly from the *NP Controller*
APK and then validated live.

The project covers the amplifier's **three channels** — YNC (`:80`),
MediaRenderer UPnP (`:8080`) and IR injection `Remote_Signal` — and uses the
MediaRenderer to **push audio** to the amplifier (the official app's
*localfilecontroller* module).

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

> Developed with Qwen3.8 Max and Qwen3.8 Flash via [Pi](https://github.com/badlogic/pi-mono), in [Zed](https://zed.dev) via [ACP](https://agentclientprotocol.com).

## Quick start

```bash
# in this repo (no installation needed)
python -c "from rn301 import discover_one, RN301; amp=RN301(discover_one()['host']); amp.power_on()"

# web server → http://localhost:8080
python -m webapp.server --port 8080 --amp 192.168.1.91

# offline tests (no amplifier required)
python -m unittest discover tests
```

Install the library in another project:

```bash
pip install -e ../rn301          # or: uv add ../rn301 --editable
```

The package exposes two CLIs: `rn301-discover` (SSDP scan → JSON) and
`rn301-gena` (live listening of GENA events). Type hints included (`py.typed`).

## `rn301/` library

Complete YNC client, ~90 operations validated live. Handles firmware quirks:
one request per POST, retry on empty/400 responses, 5 s delay on RC=3
(amplifier "busy" after a network source switch).

```python
from rn301 import RN301, discover_one
amp = RN301(discover_one()["host"])

amp.power_on(); amp.set_volume(25); amp.set_mute(True)
amp.set_input("SERVER")                    # 11 sources available
amp.playback("SERVER", "Play")            # Play/Stop/Pause/Skip Fwd/Skip Rev
amp.set_shuffle("SERVER", True)
amp.cursor("SERVER", "Down")              # DLNA / Net Radio menu navigation
amp.tune_fm(99.1); amp.select_preset(3)   # FM/AM tuner
amp.remote_key("shuffle")                  # IR injection (device 7F01)

# escape hatch
amp.get("Main_Zone/Power_Control/Power")
amp.put("Main_Zone/Input/Input_Sel", "CD")
```

Full reference: [`docs/YNC_COMMANDS.md`](docs/YNC_COMMANDS.md).

## `webapp/` web server

Simple, responsive, self-contained control page (no external dependency) +
JSON API. Auto theme (dark/light). Auto-refresh every 4 s, volume/mute pushed
via GENA over SSE (`/api/events`).

Features: power, volume, mute, 11 sources, now-playing (cover art, play/pause/
skip, shuffle/repeat), tuner, clickable DLNA/Net-Radio browser, audio push,
Network Standby, IR keys.

## DLNA push

The R-N301 exposes a MediaRenderer UPnP on `:8080`. The project serves the file
over HTTP (Range/206) then sends `SetAVTransportURI` + `Play` via SOAP. The
amplifier becomes an **audio source**: TTS, arbitrary webradios, files pushed
from a phone — anything becomes possible.

```python
from rn301 import PushServer
push = PushServer("192.168.1.91")
push.push_file("track.flac", title="Track", artist="Me")
push.wait_stopped(timeout=600)    # blocks on the GENA event, zero polling
push.close()
```

Supported audio formats: WAV, MP3, FLAC, MP4/M4A (the audio of an MP4 video is
played), ALAC. Not supported: WMA, OGG/Opus (the renderer shows `PLAYING` but
no sound comes out).

Firmware constraints: [`docs/DLNA_PUSH.md`](docs/DLNA_PUSH.md).

## Virtual remote (coming soon)

The IR device `7F01` was scanned exhaustively (256/256 OBC accepted by the
firmware). Next step: capture the physical RAX31 remote codes (volume, power,
Speaker A/B, Balance…) to extend `remote_key()` with the functions not exposed
over YNC.

## Reverse engineering

The R-N301 exposes **no YNC descriptor** (`desc.xml` → 404): the official app
hard-codes commands per model. Method: APK decompilation (androguard), live
probing, IR brute-force, cross-validation. Analysis scripts documented in
[`tools/README.md`](tools/README.md), full journal in
[`docs/RESEARCH.md`](docs/RESEARCH.md).

## Project layout

```
├── rn301/               # library
│   ├── rn301.py         #   YNC client (~90 ops, retries, lock)
│   ├── push.py          #   DLNA push (FileServer + SOAP + GENA events)
│   ├── gena.py          #   GENA subscriber
│   ├── discover.py      #   SSDP discovery
│   └── _locks.py        #   per-amplifier serialization
├── webapp/              # web server + control page + JSON API
├── tests/               # offline tests (unittest + mock UPnP renderer)
├── docs/                # advanced documentation
├── tools/               # analysis scripts → see tools/README.md
└── amp/                 # archived live responses (probes, IR scans, SCPDs)
```

## Warnings

- Hobby project, not affiliated with Yamaha. YNC is not officially documented.
- The firmware is finicky: always expect retries (the library handles this).
- `System/Misc/Update` can trigger a firmware update — use only with full
  awareness.
- NET RADIO: the embedded vTuner service requires a paid account (fewer
  stations) — a Yamaha/vTuner matter, not a protocol issue.

## License

MIT (see `LICENSE`). The Yamaha APK and PDFs remain Yamaha's property — not
redistributed here.
