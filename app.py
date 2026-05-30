from flask import Flask, render_template_string, jsonify, request, send_file
import json
import shutil
import re
import time
import subprocess
import os
import sys
import threading
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime

app = Flask(__name__)

# Sicherheits-Guard: Nur lokale Requests erlauben fuer gefaehrliche Endpoints
def _local_only():
    """Blockiert non-localhost Requests mit 403."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        from flask import abort
        abort(403)

OUTPUT_DIR = Path(__file__).parent / "output"
SN_RAW_FILE = OUTPUT_DIR / "sprachnudel_raw.jsonl"
SN_RAW_SNAPSHOT_FILE = OUTPUT_DIR / "sprachnudel_raw.snapshot.jsonl"
SN_RAW_SNAPSHOT_V8_FILE = OUTPUT_DIR / "sprachnudel_raw.snapshot.v11.merged.jsonl"
SN_EXPORT_V12_FILE = OUTPUT_DIR / "sprachnudel_export.v12.json"
SN_CACHE_VERSION = 12

# v12 Daten: Lazy-Loading Index (thread-safe)
_v12_index: dict[str, dict] | None = None   # suchwort_norm.casefold() → word entry
_v12_topic_index: dict[str, list[str]] | None = None  # thema → [suchwort_norm, ...]
_v12_loaded = False
_v12_lock = threading.Lock()


def _load_v12() -> bool:
    """Laedt den v12 Export und baut den Such-Index auf (thread-safe)."""
    global _v12_index, _v12_topic_index, _v12_loaded
    if _v12_loaded:
        return _v12_index is not None
    with _v12_lock:
        # Double-check nach Lock-Erwerb
        if _v12_loaded:
            return _v12_index is not None
        if not SN_EXPORT_V12_FILE.exists():
            print(f"WARNUNG: {SN_EXPORT_V12_FILE.name} nicht gefunden")
            _v12_loaded = True
            return False
        size_mb = SN_EXPORT_V12_FILE.stat().st_size / 1024 / 1024
        print(f"Lade v12 Export ({size_mb:.0f} MB)...")
        t0 = time.time()
        with open(SN_EXPORT_V12_FILE, "r", encoding="utf-8") as f:
            export = json.load(f)
        _v12_index = {}
        _v12_topic_index = {}
        for w in export.get("words", []):
            key = w.get("suchwort_norm", "").strip().casefold()
            if not key:
                continue
            _v12_index[key] = w
            for topic in w.get("themen", []):
                _v12_topic_index.setdefault(topic, []).append(key)
        stats = export.get("stats", {})
        elapsed = time.time() - t0
        print(f"v12 geladen: {stats.get('word_count', 0)} Woerter, "
              f"{stats.get('words_with_rhymes', 0)} mit Reimen, "
              f"{len(_v12_topic_index)} Themen in {elapsed:.1f}s")
        _v12_loaded = True
        return True


def _get_v12_word(word: str) -> dict | None:
    """Sucht ein Wort im v12 Index."""
    if not _load_v12():
        return None
    return _v12_index.get(word.strip().casefold())

SN_SILBEN_MAP = {
    "Mit einer Silbe": 1, "Mit 2 Silben": 2, "Mit 3 Silben": 3,
    "Mit 4 Silben": 4, "Mit 5 Silben": 5, "Mit 6 Silben": 6,
    "Mit 7 Silben": 7, "Mit 8 Silben": 8, "Mit 9 Silben": 9,
}
SN_RESULT_CLASSES = {"mb-2", "ms-2"}
SN_ALLOWED_WORTARTEN = {"allgemein", "adjektive", "substantive", "verben"}


def _count_silben(wort: str) -> int:
    vokale = "aeiouäöüy"
    count, prev = 0, False
    for ch in wort.lower():
        is_v = ch in vokale
        if is_v and not prev:
            count += 1
        prev = is_v
    return max(1, count)


def _sn_ensure_snapshot() -> Path | None:
    if SN_RAW_SNAPSHOT_V8_FILE.exists():
        return SN_RAW_SNAPSHOT_V8_FILE
    if SN_RAW_SNAPSHOT_FILE.exists():
        return SN_RAW_SNAPSHOT_FILE
    if not SN_RAW_FILE.exists():
        return None
    shutil.copy2(SN_RAW_FILE, SN_RAW_SNAPSHOT_FILE)
    return SN_RAW_SNAPSHOT_FILE


def _sn_group_results(results: list[dict]) -> list[dict]:
    wortart_order = ["allgemein", "adjektive", "substantive", "verben", "unbekannt"]
    by_wortart: dict[str, dict[int, list[str]]] = {}
    seen_per_group: set[tuple[str, int, str]] = set()

    for item in results:
        wort = (item.get("wort") or "").strip()
        if not wort:
            continue
        wortart = (item.get("wortart") or "unbekannt").lower()
        if wortart == "suchwort":
            continue
        if wortart not in wortart_order:
            wortart = "unbekannt"
        silben = int(item.get("silben") or _count_silben(wort))

        by_wortart.setdefault(wortart, {})
        by_wortart[wortart].setdefault(silben, [])

        dedupe_key = (wortart, silben, wort.casefold())
        if dedupe_key in seen_per_group:
            continue
        seen_per_group.add(dedupe_key)
        by_wortart[wortart][silben].append(wort)

    kategorien = []
    for wortart in wortart_order:
        if wortart not in by_wortart:
            continue
        silben_map = by_wortart[wortart]
        gruppen = [
            {"silben": silben, "woerter": woerter}
            for silben, woerter in sorted(silben_map.items(), reverse=True)
        ]
        kategorien.append({"wortart": wortart, "gruppen": gruppen})

    return kategorien


def _sn_find_raw_entry(q: str):
    source_file = _sn_ensure_snapshot()
    if not source_file:
        return None

    key = q.strip().casefold()
    latest_match = None
    latest_positive_match = None

    with open(source_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if (entry.get("suchwort") or "").strip().casefold() != key:
                continue
            latest_match = entry
            results = entry.get("results", [])
            if len(results) > 1 and entry.get("count", 0) > 0:
                latest_positive_match = entry

    return latest_positive_match or latest_match


def _sn_search_raw(q: str):
    entry = _sn_find_raw_entry(q)
    if not entry:
        return None

    results = [
        item for item in entry.get("results", [])
        if (item.get("wortart") or "").lower() != "suchwort"
    ]
    if not results:
        return None

    return {
        "suchwort": q,
        "total": len(results),
        "kategorien": _sn_group_results(results),
        "cached": "snapshot",
        "_cache_version": SN_CACHE_VERSION,
    }


RESCRAPE_PROGRESS_FILE = OUTPUT_DIR / "sn_rescrape_progress.json"
RESCRAPE_PATCH_FILE = OUTPUT_DIR / "sprachnudel_rescrape_patch.jsonl"
RESCRAPE_V2_PROGRESS_FILE = OUTPUT_DIR / "sn_rescrape_progress.json"
RESCRAPE_V2_PATCH_FILE = OUTPUT_DIR / "sprachnudel_rescrape_v2_patch.jsonl"
RESCRAPE_V2_WORDS_FILE = OUTPUT_DIR / "rescrape_v1_full.json"
DEREWO_PROGRESS_FILE = OUTPUT_DIR / "sn_derewo_progress.json"
DEREWO_PATCH_FILE = OUTPUT_DIR / "sprachnudel_derewo_patch.jsonl"
DEREWO_CONTROL_FILE = OUTPUT_DIR / "sn_derewo_control.json"
DEREWO_WORDS_FILE = OUTPUT_DIR / "missing_derewo_common.json"
BAUERNSPRUCH_PROGRESS_FILE = OUTPUT_DIR / "sn_bauernspruch_progress.json"
BAUERNSPRUCH_PATCH_FILE = OUTPUT_DIR / "sprachnudel_bauernspruch_patch.jsonl"
BAUERNSPRUCH_CONTROL_FILE = OUTPUT_DIR / "sn_bauernspruch_control.json"
BAUERNSPRUCH_WORDS_FILE = OUTPUT_DIR / "missing_bauernspruch_words.json"
SITEMAP_PROGRESS_FILE = OUTPUT_DIR / "sn_sitemap_progress.json"
SITEMAP_PATCH_FILE = OUTPUT_DIR / "sprachnudel_sitemap_patch.jsonl"
SITEMAP_CONTROL_FILE = OUTPUT_DIR / "sn_sitemap_control.json"
COUNTZERO_PROGRESS_FILE = OUTPUT_DIR / "sn_countzero_progress.json"
COUNTZERO_PATCH_FILE = OUTPUT_DIR / "sprachnudel_countzero_patch.jsonl"
COUNTZERO_CONTROL_FILE = OUTPUT_DIR / "sn_countzero_control.json"
SNOWBALL_PROGRESS_FILE = OUTPUT_DIR / "sn_snowball_progress.json"
SNOWBALL_PATCH_FILE = OUTPUT_DIR / "sprachnudel_snowball_patch.jsonl"
SNOWBALL_CONTROL_FILE = OUTPUT_DIR / "sn_snowball_control.json"
SNOWBALL_FRONTIER_FILE = OUTPUT_DIR / "sn_snowball_frontier.json"
COMPLETE_PROGRESS_FILE = OUTPUT_DIR / "sn_complete_progress.json"
COMPLETE_PATCH_FILE = OUTPUT_DIR / "sprachnudel_complete_patch.jsonl"
COMPLETE_LOG_FILE = OUTPUT_DIR / "sn_complete_log.txt"
RESCRAPE_CONTROL_FILE = OUTPUT_DIR / "sn_rescrape_control.json"

_rescrape_proc = None

MONITOR_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scraping Monitor</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0c0c18;--surface:#141428;--surface2:#1c1c36;--border:#2a2a50;--accent:#7c6bff;--accent2:#ff6b9d;--green:#4ecdc4;--yellow:#e8d44d;--red:#ff5555;--blue:#5599ff;--text:#e0e0f0;--muted:#777}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}

.topbar{background:var(--surface);border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;height:56px;gap:16px}
.topbar h1{font-size:1.2rem;color:var(--accent)}
.topbar h1 span{color:var(--accent2)}
.topbar a{color:var(--muted);text-decoration:none;font-size:.85rem}
.topbar a:hover{color:var(--accent)}

.page{padding:20px 24px;flex:1;overflow-y:auto}

.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px}
.card h2{font-size:1rem;color:var(--accent);margin-bottom:12px;display:flex;align-items:center;gap:8px}
.card h2 .badge{font-size:.7rem;padding:2px 8px;border-radius:10px;font-weight:700}
.badge-green{background:rgba(78,205,196,.2);color:var(--green)}
.badge-yellow{background:rgba(232,212,77,.2);color:var(--yellow)}
.badge-red{background:rgba(255,85,85,.2);color:var(--red)}
.badge-blue{background:rgba(85,153,255,.2);color:var(--blue)}
.badge-gray{background:rgba(119,119,119,.2);color:var(--muted)}

.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px}
.stat-box{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.stat-box .value{font-size:1.6rem;font-weight:700;line-height:1.2}
.stat-box .label{font-size:.75rem;color:var(--muted);margin-top:4px}
.val-green{color:var(--green)}.val-yellow{color:var(--yellow)}.val-red{color:var(--red)}.val-blue{color:var(--blue)}.val-accent{color:var(--accent)}

.progress-bar{width:100%;height:24px;background:var(--bg);border-radius:12px;overflow:hidden;border:1px solid var(--border);margin:8px 0}
.progress-fill{height:100%;border-radius:12px;transition:width .5s ease;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;color:#fff}
.fill-green{background:linear-gradient(90deg,#2d8f88,#4ecdc4)}
.fill-yellow{background:linear-gradient(90deg,#b8a030,#e8d44d)}
.fill-blue{background:linear-gradient(90deg,#3366cc,#5599ff)}

.sample-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-top:12px}
.sample-item{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center}
.sample-item .word{font-weight:600;font-size:.9rem}
.sample-item .count{font-size:.85rem;font-weight:700}
.count-high{color:var(--green)}.count-mid{color:var(--yellow)}.count-low{color:var(--muted)}

.log-box{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;max-height:300px;overflow-y:auto;font-family:'Consolas','Courier New',monospace;font-size:.8rem;line-height:1.6}
.log-line{color:var(--muted)}.log-line-ok{color:var(--green)}.log-line-err{color:var(--red)}.log-line-warn{color:var(--yellow)}

.refresh-info{font-size:.8rem;color:var(--muted);text-align:right;margin-top:8px}

.ctrl-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.btn{padding:10px 20px;border-radius:10px;border:1px solid var(--border);background:var(--surface2);color:var(--text);font-size:.85rem;cursor:pointer;font-weight:600;transition:all .15s;display:inline-flex;align-items:center;gap:6px}
.btn:hover:not(:disabled){border-color:var(--accent);transform:translateY(-1px)}
.btn:disabled{opacity:.35;cursor:not-allowed}
.btn-start{border-color:var(--green);color:var(--green)}
.btn-start:hover:not(:disabled){background:rgba(78,205,196,.15)}
.btn-pause{border-color:var(--yellow);color:var(--yellow)}
.btn-pause:hover:not(:disabled){background:rgba(232,212,77,.15)}
.btn-resume{border-color:var(--blue);color:var(--blue)}
.btn-resume:hover:not(:disabled){background:rgba(85,153,255,.15)}
.btn-stop{border-color:var(--red);color:var(--red)}
.btn-stop:hover:not(:disabled){background:rgba(255,85,85,.15)}
.btn-test{border-color:var(--accent);color:var(--accent)}
.btn-test:hover:not(:disabled){background:rgba(124,107,255,.15)}

.toast{position:fixed;top:16px;right:16px;padding:14px 20px;border-radius:10px;font-size:.9rem;font-weight:600;z-index:9999;animation:slideIn .3s ease;max-width:400px}
.toast-ok{background:rgba(78,205,196,.15);border:1px solid var(--green);color:var(--green)}
.toast-err{background:rgba(255,85,85,.15);border:1px solid var(--red);color:var(--red)}
.toast-info{background:rgba(85,153,255,.15);border:1px solid var(--blue);color:var(--blue)}
@keyframes slideIn{from{transform:translateX(100px);opacity:0}to{transform:translateX(0);opacity:1}}

.test-result{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-top:6px}
.test-result .tr-word{font-weight:700;font-size:.95rem}
.test-result .tr-status{font-size:.85rem;font-weight:600;margin-left:8px}
.test-result .tr-detail{font-size:.8rem;color:var(--muted);margin-left:8px}
.test-result .tr-sample{font-size:.8rem;color:var(--accent);margin-top:4px}
.tr-ok{color:var(--green)}.tr-leer{color:var(--muted)}.tr-err{color:var(--red)}

.status-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-running{background:var(--green);box-shadow:0 0 6px var(--green)}
.dot-paused{background:var(--yellow);box-shadow:0 0 6px var(--yellow)}
.dot-idle{background:var(--muted)}
.dot-stopped{background:var(--red)}
.dot-done{background:var(--blue)}

.test-input{width:60px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.85rem;text-align:center}
.test-input:focus{outline:none;border-color:var(--accent)}
</style>
</head>
<body>

<div class="topbar">
  <h1>Scraping <span>Monitor</span></h1>
  <a href="/">← Reimsuche</a>
</div>

<div class="page">

  <div class="card">
    <h2>Steuerung <span class="badge badge-gray" id="ctrlBadge"><span class="status-dot dot-idle" id="ctrlDot"></span>Prüfe...</span></h2>
    <div class="ctrl-row">
      <button class="btn btn-start" id="btnStart" onclick="ctrlStart()" disabled>▶ Start</button>
      <button class="btn btn-pause" id="btnPause" onclick="ctrlPause()" disabled>⏸ Pause</button>
      <button class="btn btn-resume" id="btnResume" onclick="ctrlResume()" disabled>▶ Fortsetzen</button>
      <button class="btn btn-stop" id="btnStop" onclick="ctrlStop()" disabled>⏹ Stop</button>
      <span style="width:1px;background:var(--border);margin:0 4px"></span>
      <button class="btn btn-test" id="btnSpeedSlow" onclick="ctrlSpeed('slow')">🐢 Langsam</button>
      <button class="btn btn-test" id="btnSpeedNormal" onclick="ctrlSpeed('normal')">⚖ Normal</button>
      <button class="btn btn-test" id="btnSpeedFast" onclick="ctrlSpeed('fast')">⚡ Schnell</button>
      <button class="btn btn-test" id="btnSpeedUltra" onclick="ctrlSpeed('ultra')" style="background:linear-gradient(135deg,#ff6b6b,#ffd700)">🚀 ULTRA x5</button>
      <span style="width:1px;background:var(--border);margin:0 4px"></span>
      <button class="btn btn-test" id="btnTest" onclick="ctrlTest()">🧪 Test</button>
      <input class="test-input" id="testN" type="number" value="10" min="1" max="50"> Wörter
    </div>
    <div style="margin-top:16px;padding:16px;border:2px solid var(--border);border-radius:12px;background:var(--surface)">
      <div style="font-size:1rem;font-weight:700;color:var(--accent);margin-bottom:12px">Scraping-Modus wählen</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <button id="btnModeBrowser" onclick="setMode('browser')"
          style="flex:1;min-width:200px;padding:14px 18px;border:3px solid transparent;border-radius:12px;background:linear-gradient(135deg,#4a2d7a,#6b3fa0);cursor:pointer;text-align:left;transition:all .2s;color:#fff">
          <div style="font-size:.7rem;font-weight:700;letter-spacing:1px;opacity:.8">MODUS 1</div>
          <div style="font-size:1.2rem;font-weight:700;margin-top:4px">🌐 BROWSER</div>
          <div style="font-size:.75rem;opacity:.8;margin-top:4px">Chrome-Fenster, manuelle CF-Lösung</div>
        </button>
        <button id="btnModeVpn" onclick="setMode('vpn')"
          style="flex:1;min-width:200px;padding:14px 18px;border:3px solid transparent;border-radius:12px;background:linear-gradient(135deg,#1a5276,#2e86c1);cursor:pointer;text-align:left;transition:all .2s;color:#fff">
          <div style="font-size:.7rem;font-weight:700;letter-spacing:1px;opacity:.8">MODUS 2</div>
          <div style="font-size:1.2rem;font-weight:700;margin-top:4px">🔒 VPN</div>
          <div style="font-size:.75rem;opacity:.8;margin-top:4px">curl_cffi + CF-Cookies + Surfshark</div>
        </button>
        <button id="btnModeDirect" onclick="setMode('direct')"
          style="flex:1;min-width:200px;padding:14px 18px;border:3px solid transparent;border-radius:12px;background:linear-gradient(135deg,#7d5a00,#b8860b);cursor:pointer;text-align:left;transition:all .2s;color:#fff">
          <div style="font-size:.7rem;font-weight:700;letter-spacing:1px;opacity:.8">MODUS 3</div>
          <div style="font-size:1.2rem;font-weight:700;margin-top:4px">📡 DIRECT</div>
          <div style="font-size:.75rem;opacity:.8;margin-top:4px">curl_cffi + CF-Cookies, manuelle IP-Erneuerung</div>
        </button>
      </div>
      <div id="currentModeDisplay" style="margin-top:10px;font-size:.9rem;color:var(--muted);text-align:center">Lade...</div>
    </div>
    <div id="ctrlMsg" style="font-size:.85rem;color:var(--muted);margin-top:8px"></div>

    <!-- VPN-STEUERUNG -->
    <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border)">
      <h3 style="font-size:.95rem;color:var(--accent);margin-bottom:8px;display:flex;align-items:center;gap:8px">
        🔐 Surfshark VPN <span id="vpnStatusBadge" style="font-size:.75rem;padding:2px 8px;border-radius:10px;background:var(--surface2);color:var(--muted)">Prüfe...</span>
      </h3>
      <div class="ctrl-row" style="flex-wrap:wrap;gap:8px">
        <button class="btn btn-test" id="btnVpnConnect" onclick="vpnConnect()" style="background:rgba(80,200,120,.15)">▶ Verbinden</button>
        <button class="btn btn-test" id="btnVpnDisconnect" onclick="vpnDisconnect()" style="background:rgba(255,100,100,.15)">■ Trennen</button>
        <span style="width:1px;background:var(--border);margin:0 4px"></span>
        <select id="vpnServerSelect" style="background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:.85rem;min-width:200px">
          <option value="">Server wählen...</option>
        </select>
        <button class="btn btn-test" onclick="vpnSwitch()" style="background:rgba(100,149,237,.15)">🔄 Wechseln</button>
        <button class="btn btn-test" onclick="vpnRotate()" style="background:rgba(255,165,0,.15)">🎲 Zufällig</button>
        <span style="width:1px;background:var(--border);margin:0 4px"></span>
        <button class="btn btn-test" onclick="vpnCheckIp()" style="font-size:.75rem;padding:4px 8px">🌐 IP prüfen</button>
        <span id="vpnIpDisplay" style="font-size:.8rem;color:var(--muted);align-self:center"></span>
        <span style="width:1px;background:var(--border);margin:0 4px"></span>
        <button class="btn btn-test" id="btnCfCookies" onclick="cfFetchCookies()" style="display:none;background:rgba(255,200,0,.2);color:#ffd700">🍪 CF-Cookies holen</button>
        <button class="btn btn-test" id="btnAdminRestart" onclick="restartAsAdmin()" style="background:rgba(255,80,80,.15);color:#ff6b6b">⚡ Neustart als Admin</button>
      </div>
      <div id="vpnInfo" style="font-size:.8rem;color:var(--muted);margin-top:6px"></div>
      <div id="cfCookieStatus" style="font-size:.8rem;margin-top:4px;display:none">
        <span style="color:#ffd700">⚠️ CF-Cookies fehlen! VPN-Modus braucht diese.</span>
      </div>
    </div>

    <!-- SPRUECHEKLOPPER wurde auf eigene Seite verschoben: /sprueche -->

    <!-- DETAILIERTER AKTIVITÄTSLOG -->
    <div style="margin-top:16px;padding-top:16px;border-top:2px solid var(--border)">
      <h3 style="font-size:.95rem;color:var(--accent);margin-bottom:8px;display:flex;align-items:center;gap:8px">
        📋 Echtzeit-Log <span style="font-size:.75rem;color:var(--muted);font-weight:normal">(letzte 50 Einträge)</span>
      </h3>
      <div id="activityLog" style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px;max-height:300px;overflow-y:auto;font-family:'Consolas','Monaco',monospace;font-size:.8rem;line-height:1.5">
        <div style="color:var(--muted)">⏳ Lade Aktivitätslog...</div>
      </div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn btn-test" onclick="clearLog()" style="font-size:.75rem;padding:4px 8px">🗑️ Log leeren</button>
        <button class="btn btn-test" onclick="exportLog()" style="font-size:.75rem;padding:4px 8px">💾 Exportieren</button>
        <span style="color:var(--muted);font-size:.75rem;margin-left:auto;align-self:center" id="logStats">0 Einträge</span>
      </div>
    </div>
  </div>

  <div class="card" id="testCard" style="display:none">
    <h2>Test-Ergebnisse</h2>
    <div id="testResults"></div>
  </div>

  <div class="card" id="rescrapeCard">
    <h2 id="rsTitle">Rescrape count=0 <span class="badge badge-green" id="rsBadge">laden...</span></h2>
    <div class="stats-grid" id="rsStats"></div>
    <div class="progress-bar"><div class="progress-fill fill-green" id="rsProgress" style="width:0%"></div></div>
    <div id="rsTimeInfo" style="font-size:.8rem;color:var(--muted)"></div>
    <h2 style="margin-top:16px">Letzte reparierte Woerter</h2>
    <div class="sample-grid" id="rsSamples"></div>
  </div>

  <div class="card" id="completeCard">
    <h2>Complete Scrape (DeReWo) <span class="badge badge-blue" id="csBadge">laden...</span></h2>
    <div class="stats-grid" id="csStats"></div>
    <div class="progress-bar"><div class="progress-fill fill-blue" id="csProgress" style="width:0%"></div></div>
  </div>

  <div class="card">
    <h2>Log (letzte 30 Zeilen)</h2>
    <div class="log-box" id="logBox">Lade...</div>
  </div>

  <div class="refresh-info" id="refreshInfo">Auto-Refresh alle 15s</div>
</div>

<script>
const REFRESH_MS=15000;
let lastRefresh=0;

async function loadData(){
  try{
    const r=await fetch('/api/scrape-status');
    const d=await r.json();
    renderRescrape(d.rescrape||{});
    renderComplete(d.complete||{});
    renderLog(d.log||[]);
    renderControl(d.control||{});
    lastRefresh=Date.now();
    document.getElementById('refreshInfo').textContent='Aktualisiert: '+new Date().toLocaleTimeString()+' | Auto-Refresh alle 15s';
  }catch(e){
    document.getElementById('refreshInfo').textContent='Fehler beim Laden: '+e.message;
  }
}

function fmtNum(n){return typeof n==='number'?n.toLocaleString('de-DE'):n||'-'}

function deltaSpan(v,opts){
  if(v==null||v===undefined)return '';
  const sign=v>0?'+':'';
  const color=v>0?'var(--green)':v<0?'var(--red)':'var(--muted)';
  const unit=(opts&&opts.unit)||'';
  const decimals=(opts&&opts.decimals!==undefined)?opts.decimals:0;
  const display=decimals>0?v.toFixed(decimals):fmtNum(Math.abs(v));
  return '<div style="font-size:.7rem;color:'+color+';margin-top:2px">'+sign+(v<0?'-':'')+display+unit+'</div>';
}

function fmtDuration(sec){
  if(!sec||sec<0)return'-';
  const h=Math.floor(sec/3600);const m=Math.floor((sec%3600)/60);const s=Math.floor(sec%60);
  if(h>0)return h+'h '+m+'min';
  if(m>0)return m+'min '+s+'s';
  return s+'s';
}

function renderControl(c){
  const st=c.status||'idle';
  const msg=c.msg||'';
  const speed=c.speed||'normal';
  const dot=document.getElementById('ctrlDot');
  const badge=document.getElementById('ctrlBadge');
  const btnS=document.getElementById('btnStart');
  const btnP=document.getElementById('btnPause');
  const btnR=document.getElementById('btnResume');
  const btnX=document.getElementById('btnStop');
  const btnSlow=document.getElementById('btnSpeedSlow');
  const btnNormal=document.getElementById('btnSpeedNormal');
  const btnFast=document.getElementById('btnSpeedFast');
  const btnUltra=document.getElementById('btnSpeedUltra');

  dot.className='status-dot dot-'+st;
  const labels={idle:'Gestoppt',running:'Laeuft',paused:'Pausiert',stopped:'Gestoppt',stop:'Gestoppt',done:'Fertig',run:'Startet...'};
  badge.innerHTML='<span class="status-dot dot-'+st+'" id="ctrlDot"></span>'+(labels[st]||st);

  btnS.disabled=!(st==='idle'||st==='stop'||st==='stopped'||st==='done');
  btnP.disabled=(st!=='running');
  btnR.disabled=(st!=='paused');
  btnX.disabled=!(st==='running'||st==='paused');
  btnSlow.style.outline=speed==='slow'?'2px solid var(--accent)':'none';
  btnNormal.style.outline=speed==='normal'?'2px solid var(--accent)':'none';
  btnFast.style.outline=speed==='fast'?'2px solid var(--accent)':'none';
  if(btnUltra)btnUltra.style.outline=speed==='ultra'?'2px solid var(--accent)':'none';

  var hint='';
  if(st==='stopped'||st==='stop'||st==='idle'||st==='done')hint=' → Klicke "▶ Start" um zu beginnen';
  else if(st==='paused')hint=' → Klicke "▶ Fortsetzen" um weiterzumachen';
  document.getElementById('ctrlMsg').textContent=msg+' | Speed: '+speed+hint;
}

function renderRescrape(d){
  const mode=d.mode||'rescrape';
  const title=document.getElementById('rsTitle');
  if(mode==='rescrape_v2'){
    title.innerHTML='Rescrape v2 — 48K verdächtige <span class="badge badge-green" id="rsBadge">'+(document.getElementById('rsBadge')?document.getElementById('rsBadge').textContent:'laden...')+'</span>';
  } else if(mode==='snowball'){
    title.innerHTML='Schneeball-Discovery <span class="badge badge-green" id="rsBadge">'+(document.getElementById('rsBadge')?document.getElementById('rsBadge').textContent:'laden...')+'</span>';
  } else if(mode==='bauernspruch'){
    title.innerHTML='Bauernspruch Wortliste <span class="badge badge-green" id="rsBadge">'+(document.getElementById('rsBadge')?document.getElementById('rsBadge').textContent:'laden...')+'</span>';
  } else if(mode==='derewo'){
    title.innerHTML='DeReWo Ergaenzungs-Scrape <span class="badge badge-green" id="rsBadge">'+(document.getElementById('rsBadge')?document.getElementById('rsBadge').textContent:'laden...')+'</span>';
  } else if(mode==='countzero'){
    title.innerHTML='Count=0 Rescrape — BROWSER <span class="badge badge-green" id="rsBadge">'+(document.getElementById('rsBadge')?document.getElementById('rsBadge').textContent:'laden...')+'</span>';
  }
  const total=d.total||0;
  const completed=d.completed||0;
  const fixed=d.fixed||0;
  const stillZero=d.still_zero||0;
  const pct=total>0?(completed/total*100):0;
  const rate=d.rate||0;
  const etaSec=d.eta_seconds||0;

  document.getElementById('rsBadge').textContent=completed>=total&&total>0?'FERTIG':'LAEUFT';
  document.getElementById('rsBadge').className='badge '+(completed>=total&&total>0?'badge-green':'badge-yellow');

  document.getElementById('rsStats').innerHTML= mode==='snowball' ? `
    <div class="stat-box"><div class="value val-green">${fmtNum(fixed)}</div><div class="label">Mit Reimen</div></div>
    <div class="stat-box"><div class="value val-blue">${fmtNum(completed)}</div><div class="label">Verarbeitet</div></div>
    <div class="stat-box"><div class="value val-red">${fmtNum(d.errors||0)}</div><div class="label">Fehler</div></div>
    <div class="stat-box"><div class="value val-yellow">${fmtNum(d.blocked||0)}</div><div class="label">Blocked</div></div>
    <div class="stat-box"><div class="value" style="color:var(--muted)">${fmtNum(d.empty||0)}</div><div class="label">Leer</div></div>
    <div class="stat-box"><div class="value val-accent">${fmtNum(d.snowball_added||0)}</div><div class="label">Neu entdeckt</div></div>
    <div class="stat-box"><div class="value val-yellow">${fmtNum(d.queue_remaining||0)}</div><div class="label">Queue</div></div>
    <div class="stat-box"><div class="value">${fmtNum(total)}</div><div class="label">Gesamt (dyn.)</div></div>
    <div class="stat-box"><div class="value val-accent">${rate.toFixed(2)}/s</div><div class="label">Rate</div></div>
    <div class="stat-box"><div class="value">${(d.error_rate||0).toFixed(1)}%</div><div class="label">Fehlerquote</div></div>
    <div class="stat-box"><div class="value">${fmtDuration(etaSec)}</div><div class="label">ETA</div></div>
    <div class="stat-box"><div class="value">${fmtDuration(d.elapsed_seconds||0)}</div><div class="label">Laufzeit</div></div>
    <div class="stat-box"><div class="value">${fmtNum(d.cookie_refreshes||0)}</div><div class="label">Cookie-Ref.</div></div>
  ` : mode==='countzero' ? (function(){
    const dl=d.delta_5min||{};
    const r5=d.rate_5min||0;
    const eta5=d.eta_5min_seconds||0;
    return `
    <div class="stat-box"><div class="value val-green">${fmtNum(fixed)}</div><div class="label">Mit Reimen</div>${deltaSpan(dl.found)}</div>
    <div class="stat-box"><div class="value val-blue">${fmtNum(completed)}</div><div class="label">Verarbeitet</div>${deltaSpan(dl.completed)}</div>
    <div class="stat-box"><div class="value">${fmtNum(total)}</div><div class="label">Gesamt</div></div>
    <div class="stat-box"><div class="value val-yellow">${fmtNum(stillZero)}</div><div class="label">Bleiben leer</div>${deltaSpan(dl.still_empty)}</div>
    <div class="stat-box"><div class="value val-red">${fmtNum(d.blocked||0)}</div><div class="label">Blocked</div>${deltaSpan(dl.blocked)}</div>
    <div class="stat-box"><div class="value" style="color:var(--muted)">${fmtNum(d.errors||0)}</div><div class="label">Fehler</div>${deltaSpan(dl.errors)}</div>
    <div class="stat-box"><div class="value val-accent">${rate.toFixed(2)}/s</div><div class="label">Rate (gesamt)</div></div>
    <div class="stat-box"><div class="value val-accent">${r5.toFixed(3)}/s</div><div class="label">Rate (5 Min)</div></div>
    <div class="stat-box"><div class="value">${fmtDuration(etaSec)}</div><div class="label">ETA (gesamt)</div></div>
    <div class="stat-box"><div class="value val-yellow">${fmtDuration(eta5)}</div><div class="label">ETA (5 Min)</div></div>
    <div class="stat-box"><div class="value">${fmtDuration(d.elapsed_seconds||0)}</div><div class="label">Laufzeit</div></div>
    `;
  })() : `
    <div class="stat-box"><div class="value val-green">${fmtNum(fixed)}</div><div class="label">Repariert</div></div>
    <div class="stat-box"><div class="value val-blue">${fmtNum(completed)}</div><div class="label">Verarbeitet</div></div>
    <div class="stat-box"><div class="value">${fmtNum(total)}</div><div class="label">Gesamt</div></div>
    <div class="stat-box"><div class="value val-yellow">${fmtNum(stillZero)}</div><div class="label">Bleiben leer</div></div>
    <div class="stat-box"><div class="value val-accent">${rate.toFixed(2)}/s</div><div class="label">Rate</div></div>
    <div class="stat-box"><div class="value">${fmtDuration(etaSec)}</div><div class="label">Gesch. Rest</div></div>
  `;

  document.getElementById('rsProgress').style.width=pct.toFixed(1)+'%';
  document.getElementById('rsProgress').textContent=pct.toFixed(1)+'%';
  if(mode==='snowball'){
    const qr=d.queue_remaining||0;
    const sa=d.snowball_added||0;
    const elSec=d.elapsed_seconds||0;
    const etaH=etaSec>0?Math.floor(etaSec/3600):0;
    const etaM=etaSec>0?Math.floor((etaSec%3600)/60):0;
    document.getElementById('rsTimeInfo').innerHTML=
      pct.toFixed(1)+'% verarbeitet | '+
      '<span style="color:var(--green)">Queue: '+fmtNum(qr)+'</span> | '+
      '<span style="color:var(--accent)">Neu: +'+fmtNum(sa)+'</span> | '+
      'Fehlerquote: '+(d.error_rate||0).toFixed(1)+'% | '+
      'Laufzeit: '+fmtDuration(elSec)+(etaSec>0?' | <span style="color:var(--yellow)">ETA: ~'+etaH+'h '+etaM+'min</span>':'');
  } else if(mode==='countzero'){
    const r5=d.rate_5min||0;
    const eta5=d.eta_5min_seconds||0;
    const dl=d.delta_5min||{};
    const dlSec=dl.seconds||300;
    let info=pct.toFixed(1)+'% abgeschlossen | ';
    info+='<span style="color:var(--accent)">Rate (5 Min): '+r5.toFixed(3)+'/s</span> | ';
    if(eta5>0){
      info+='<span style="color:var(--yellow)">ETA: '+fmtDuration(eta5)+'</span> | ';
    }
    info+='Laufzeit: '+fmtDuration(d.elapsed_seconds||0);
    if(dl.completed){
      info+=' | <span style="color:var(--green)">5 Min: +'+dl.completed+' Wörter</span>';
    }
    document.getElementById('rsTimeInfo').innerHTML=info;
  } else {
    document.getElementById('rsTimeInfo').textContent=pct.toFixed(1)+'% abgeschlossen';
  }

  const samples=d.latest_fixed||[];
  if(samples.length>0){
    let html='';
    for(const s of samples){
      const cls=s.count>=50?'count-high':s.count>=10?'count-mid':'count-low';
      html+=`<div class="sample-item"><span class="word">${esc(s.word)}</span><span class="count ${cls}">${s.count} Reime</span></div>`;
    }
    document.getElementById('rsSamples').innerHTML=html;
  }
}

function renderComplete(d){
  const total=d.total||0;
  const completed=d.completed||0;
  const success=d.success||0;
  const pct=total>0?(completed/total*100):0;
  const etaSec=d.eta_seconds||0;

  document.getElementById('csBadge').textContent=completed>=total&&total>0?'FERTIG':completed>0?'LAEUFT':'GESTOPPT';
  document.getElementById('csBadge').className='badge '+(completed>=total&&total>0?'badge-green':completed>0?'badge-blue':'badge-yellow');

  document.getElementById('csStats').innerHTML=`
    <div class="stat-box"><div class="value val-green">${fmtNum(success)}</div><div class="label">Mit Reimen</div></div>
    <div class="stat-box"><div class="value val-blue">${fmtNum(completed)}</div><div class="label">Verarbeitet</div></div>
    <div class="stat-box"><div class="value">${fmtNum(total)}</div><div class="label">Gesamt</div></div>
    <div class="stat-box"><div class="value val-accent">${(d.rate||0).toFixed(2)}/s</div><div class="label">Rate</div></div>
    <div class="stat-box"><div class="value">${fmtDuration(etaSec)}</div><div class="label">Gesch. Rest</div></div>
  `;

  document.getElementById('csProgress').style.width=Math.min(pct,100).toFixed(2)+'%';
  document.getElementById('csProgress').textContent=pct.toFixed(2)+'%';
}

function renderLog(lines){
  let html='';
  for(const line of (lines||[])){
    let cls='log-line';
    if(line.includes('FERTIG')||line.includes('ok='))cls='log-line-ok';
    else if(line.includes('Fehler')||line.includes('Error')||line.includes('403')||line.includes('429'))cls='log-line-err';
    else if(line.includes('Tempo')||line.includes('Cooldown'))cls='log-line-warn';
    html+='<div class="'+cls+'">'+esc(line)+'</div>';
  }
  document.getElementById('logBox').innerHTML=html||'<div style="color:var(--muted)">Kein Log vorhanden</div>';
}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function showToast(msg,type){
  const t=document.createElement('div');
  t.className='toast toast-'+type;
  t.textContent=msg;
  document.body.appendChild(t);
  setTimeout(()=>t.remove(),4000);
}

async function apiCall(url,msg,body=null){
  try{
    const opts={method:'POST',headers:{'Content-Type':'application/json'}};
    if(body!==null) opts.body=JSON.stringify(body);
    const r=await fetch(url,opts);
    const d=await r.json();
    if(d.ok){
      showToast(msg,'ok');
      setTimeout(loadData,500);
    }else{
      showToast(d.error||'Fehler','err');
    }
  }catch(e){
    showToast('Netzwerkfehler: '+e.message,'err');
  }
}

function ctrlStart(){apiCall('/api/scrape/rescrape/start','Rescrape gestartet')}
function ctrlPause(){apiCall('/api/scrape/rescrape/pause','Rescrape pausiert')}
function ctrlResume(){apiCall('/api/scrape/rescrape/resume','Rescrape fortgesetzt')}
function ctrlStop(){apiCall('/api/scrape/rescrape/stop','Rescrape gestoppt')}
function ctrlSpeed(speed){apiCall('/api/scrape/rescrape/speed','Geschwindigkeit gesetzt: '+speed,{speed:speed})}

async function ctrlTest(){
  const n=parseInt(document.getElementById('testN').value)||10;
  document.getElementById('btnTest').disabled=true;
  document.getElementById('testCard').style.display='block';
  document.getElementById('testResults').innerHTML='<div style="color:var(--muted)">Test laeuft... ('+n+' Woerter)</div>';
  try{
    const r=await fetch('/api/scrape/rescrape/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({n:n})});
    const d=await r.json();
    if(d.ok){
      let html='';
      let nOk=0,nLeer=0,nErr=0;
      for(const r of d.results){
        const sc=r.status==='repariert'?'tr-ok':r.status==='leer'?'tr-leer':'tr-err';
        if(r.status==='repariert')nOk++;
        else if(r.status==='leer')nLeer++;
        else nErr++;
        html+='<div class="test-result">';
        html+='<span class="tr-word">'+esc(r.word)+'</span>';
        html+='<span class="tr-status '+sc+'">'+esc(r.status)+'</span>';
        html+='<span class="tr-detail">'+esc(r.detail||'')+'</span>';
        if(r.sample&&r.sample.length>0){
          html+='<div class="tr-sample">Reime: '+r.sample.map(esc).join(', ')+'</div>';
        }
        html+='</div>';
      }
      html='<div style="margin-bottom:10px;font-size:.9rem"><strong>'+d.results.length+'</strong> getestet: <span style="color:var(--green)">'+nOk+' repariert</span> | <span style="color:var(--muted)">'+nLeer+' leer</span> | <span style="color:var(--red)">'+nErr+' Fehler</span></div>'+html;
      document.getElementById('testResults').innerHTML=html;
      showToast('Test abgeschlossen: '+nOk+'/'+d.results.length+' repariert','info');
    }else{
      document.getElementById('testResults').innerHTML='<div style="color:var(--red)">Fehler: '+esc(d.error||'unbekannt')+'</div>';
    }
  }catch(e){
    document.getElementById('testResults').innerHTML='<div style="color:var(--red)">Fehler: '+esc(e.message)+'</div>';
  }
  document.getElementById('btnTest').disabled=false;
}

// Modus-Steuerung
async function loadMode(){
  try{
    const r=await fetch('/api/scrape/countzero/mode');
    const d=await r.json();
    if(d.ok){
      updateModeButtons(d.mode);
    }
  }catch(e){
    console.error('Fehler beim Laden des Modus:', e);
  }
}

function updateModeButtons(mode){
  const btnBrowser=document.getElementById('btnModeBrowser');
  const btnVpn=document.getElementById('btnModeVpn');
  const btnDirect=document.getElementById('btnModeDirect');
  const display=document.getElementById('currentModeDisplay');

  // Alle zurücksetzen (Gradients bleiben, Border transparent)
  if(btnBrowser){btnBrowser.style.borderColor='transparent';btnBrowser.style.boxShadow='none';}
  if(btnVpn){btnVpn.style.borderColor='transparent';btnVpn.style.boxShadow='none';}
  if(btnDirect){btnDirect.style.borderColor='transparent';btnDirect.style.boxShadow='none';}

  if(mode==='vpn'){
    btnVpn.style.borderColor='#5dade2';
    btnVpn.style.boxShadow='0 0 20px rgba(46,134,193,.6)';
    if(display)display.textContent='Aktiv: MODUS 2 — 🔒 VPN (curl_cffi + Surfshark)';
  }else if(mode==='direct'){
    btnDirect.style.borderColor='#f0c040';
    btnDirect.style.boxShadow='0 0 20px rgba(184,134,11,.6)';
    if(display)display.textContent='Aktiv: MODUS 3 — 📡 DIRECT (ISP-Reconnect)';
  }else{
    btnBrowser.style.borderColor='#9b7dd4';
    btnBrowser.style.boxShadow='0 0 20px rgba(107,63,160,.6)';
    if(display)display.textContent='Aktiv: MODUS 1 — 🌐 BROWSER (Chrome)';
  }
}

async function setMode(mode){
  const statusEl=document.getElementById('statusText');
  const currentStatus=statusEl?statusEl.textContent.trim():'';
  const isRunning=currentStatus.includes('Running')||currentStatus.includes('Paused');
  const hint=mode==='vpn'
    ?'🔒 VPN-Modus: Benötigt aktuelle CF-Cookies! Falls diese abgelaufen sind, starte cf_solver.py --direct-solve'
    :mode==='direct'
    ?'📡 DIRECT-Modus: curl_cffi mit CF-Cookies, ohne VPN. Bei Blockade pausiert der Scraper und wartet auf deine manuelle IP-Erneuerung (ISP-Reconnect).'
    :'🌐 BROWSER-Modus: Chrome-Fenster öffnet sich. Du musst ggf. Cloudflare-Challenges manuell bestätigen.';
  if(!confirm(`Modus wechseln zu ${mode.toUpperCase()}?\n\n${hint}\n\n${isRunning?'⚠️ Der laufende Scraper wird gestoppt und im neuen Modus neu gestartet!':''}`)){
    return;
  }
  try{
    const modeR=await fetch('/api/scrape/countzero/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});
    const modeD=await modeR.json();
    if(!modeD.ok){
      showToast('Fehler: '+(modeD.error||'unbekannt'),'err');
      return;
    }
    showToast(`Modus gesetzt auf ${mode.toUpperCase()}`,'ok');
    updateModeButtons(mode);
    if(isRunning){
      try{
        await fetch('/api/scrape/rescrape/stop',{method:'POST',headers:{'Content-Type':'application/json'}});
        await new Promise(r=>setTimeout(r,1500));
      }catch(e){}
    }
    const startR=await fetch('/api/scrape/rescrape/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,force:true})});
    const startD=await startR.json();
    if(startD.ok){
      showToast(`Scraper gestartet im ${mode.toUpperCase()}-Modus (PID: ${startD.pid})`,'ok');
      setTimeout(loadData,500);
    }else{
      showToast('Start fehlgeschlagen: '+(startD.error||'unbekannt'),'err');
    }
  }catch(e){
    showToast('Fehler: '+e.message,'err');
  }
}

// Ursprüngliche ctrlStart-Funktion erweitern
ctrlStart=async function(){
  let mode='browser';
  try{
    const r=await fetch('/api/scrape/countzero/mode');
    const d=await r.json();
    if(d.ok)mode=d.mode;
  }catch(e){}
  apiCall('/api/scrape/rescrape/start','Scrape gestartet ('+mode.toUpperCase()+'-Modus)',{mode,force:true});
};

loadMode();
loadData();
setInterval(loadData,REFRESH_MS);

// ─── VPN-Steuerung ────────────────────────────────────────
async function vpnLoadStatus(){
  try{
    const r=await fetch('/api/vpn/status');const d=await r.json();
    const badge=document.getElementById('vpnStatusBadge');
    const info=document.getElementById('vpnInfo');
    if(!d.ok){if(badge)badge.textContent='Fehler';return;}
    if(!d.is_admin){
      if(info)info.innerHTML='<span style="color:var(--muted);font-size:.8rem">Hinweis: Kein Admin. VPN-Tunnel-Start erfordert Admin-Rechte — nutze ⚡ Neustart als Admin unten.</span>';
      if(badge){badge.textContent='Kein Admin';badge.style.background='rgba(255,165,0,.2)';badge.style.color='#ffa500';}
    } else if(d.connected){
      const loc=d.tunnel_name||d.adapter?.Name||'?';
      if(badge){badge.textContent='Verbunden: '+loc;badge.style.background='rgba(78,205,196,.2)';badge.style.color='var(--green)';}
      const peer=d.peer||{};
      const endpoint=peer.endpoint||'unbekannt';
      const rx=peer['latest handshake']||'';
      if(info)info.textContent=`📍 Standort: ${loc} | Endpoint: ${endpoint}`+(rx?` | Handshake: ${rx}`:'');
    }else{
      if(badge){badge.textContent='Getrennt';badge.style.background='var(--surface2)';badge.style.color='var(--muted)';}
      if(info)info.textContent=d.error||'Nicht verbunden';
    }
  }catch(e){}
}

async function vpnLoadConfigs(){
  try{
    const r=await fetch('/api/vpn/configs');const d=await r.json();
    const sel=document.getElementById('vpnServerSelect');
    if(!sel||!d.ok)return;
    sel.innerHTML='<option value="">Server wählen... ('+d.count+')</option>';
    (d.configs||[]).forEach(c=>{
      const opt=document.createElement('option');opt.value=c.name;opt.textContent=c.name;
      sel.appendChild(opt);
    });
  }catch(e){}
}

async function vpnConnect(){
  const sel=document.getElementById('vpnServerSelect');
  const config=sel?sel.value:'';
  try{
    const r=await fetch('/api/vpn/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config})});
    const d=await r.json();
    showToast(d.ok?d.message:'Fehler: '+(d.error||'unbekannt'),d.ok?'ok':'err');
    setTimeout(()=>{vpnLoadStatus();vpnCheckIp();},2000);
  }catch(e){showToast('VPN Fehler: '+e.message,'err');}
}

async function vpnDisconnect(){
  try{
    const r=await fetch('/api/vpn/disconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});
    const d=await r.json();
    const msg=d.msg||d.message||'Getrennt';
    showToast(d.ok?msg:'Fehler: '+(d.error||'unbekannt'),d.ok?'ok':'err');
    setTimeout(()=>{vpnLoadStatus();vpnCheckIp();},2500);
  }catch(e){showToast('VPN Fehler: '+e.message,'err');}
}

async function vpnSwitch(){
  const sel=document.getElementById('vpnServerSelect');
  const config=sel?sel.value:'';
  if(!config){showToast('Bitte Server auswählen','err');return;}
  try{
    const r=await fetch('/api/vpn/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config})});
    const d=await r.json();
    showToast(d.ok?d.message:'Fehler: '+(d.error||'unbekannt'),d.ok?'ok':'err');
    setTimeout(()=>{vpnLoadStatus();vpnCheckIp();},2500);
  }catch(e){showToast('VPN Fehler: '+e.message,'err');}
}

async function vpnCheckIp(){
  const display=document.getElementById('vpnIpDisplay');
  if(display)display.textContent='Prüfe IP...';
  try{
    const r=await fetch('/api/vpn/ip');const d=await r.json();
    if(display)display.textContent=d.ok?('IP: '+d.ip):'IP: Fehler';
  }catch(e){if(display)display.textContent='IP: Fehler';}
}

async function restartAsAdmin(){
  if(!confirm('Flask als Admin neu starten?\n\nDas startet einen neuen Prozess mit Admin-Rechten. Der aktuelle Prozess wird beendet.'))return;
  try{
    const r=await fetch('/api/restart-admin',{method:'POST'});
    const d=await r.json();
    if(d.ok){
      showToast('Admin-Neustart eingeleitet. Seite in 5s neu laden...','ok');
      setTimeout(()=>location.reload(),5000);
    }else{
      showToast('Fehler: '+(d.error||'unbekannt'),'err');
    }
  }catch(e){showToast('Fehler beim Admin-Neustart','err');}
}

async function vpnRotate(){
  try{
    showToast('VPN rotiere zu zufälligem Server...','ok');
    const r=await fetch('/api/vpn/rotate',{method:'POST',headers:{'Content-Type':'application/json'}});
    const d=await r.json();
    showToast(d.ok?`Rotiert zu ${d.server||'?'} (IP: ${d.new_ip||'?'})`:'Fehler: '+(d.error||'unbekannt'),d.ok?'ok':'err');
    setTimeout(()=>{vpnLoadStatus();vpnCheckIp();},2500);
  }catch(e){showToast('VPN Fehler: '+e.message,'err');}
}

vpnLoadStatus();
vpnLoadConfigs();
vpnCheckIp();
cfCheckCookies();
setInterval(vpnLoadStatus,15000);
setInterval(cfCheckCookies,60000);

async function cfCheckCookies(){
  try{
    const r=await fetch('/api/cf/cookies/status');const d=await r.json();
    const btn=document.getElementById('btnCfCookies');
    const status=document.getElementById('cfCookieStatus');
    if(!d.ok)return;
    if(d.has_cookies){
      if(btn)btn.style.display='none';
      if(status)status.style.display='none';
    }else{
      if(btn)btn.style.display='inline-block';
      if(status)status.style.display='block';
    }
  }catch(e){}
}

async function cfFetchCookies(){
  try{
    const btn=document.getElementById('btnCfCookies');
    if(btn)btn.disabled=true;
    const r=await fetch('/api/cf/cookies/fetch',{method:'POST',headers:{'Content-Type':'application/json'}});
    const d=await r.json();
    showToast(d.message||'CF-Solver gestartet, warte auf Cookies...','ok');
    let attempts=0;const maxAttempts=30;
    const poll=setInterval(async()=>{
      attempts++;
      try{
        const sr=await fetch('/api/cf/cookies/status');const sd=await sr.json();
        if(sd.has_cookies){
          clearInterval(poll);
          if(btn)btn.disabled=false;
          showToast('CF-Cookies erfolgreich geholt! ('+sd.count+' Cookies)','ok');
          cfCheckCookies();
        }else if(attempts>=maxAttempts){
          clearInterval(poll);
          if(btn)btn.disabled=false;
          showToast('Timeout - Cookies konnten nicht geholt werden. Bitte manuell pruefen.','err');
          cfCheckCookies();
        }
      }catch(e){
        if(attempts>=maxAttempts){clearInterval(poll);if(btn)btn.disabled=false;}
      }
    },3000);
  }catch(e){showToast('Fehler: '+e.message,'err');const btn=document.getElementById('btnCfCookies');if(btn)btn.disabled=false;}
}

/* === Echtzeit-Aktivitaetslog === */
let _activityLogEntries=[];

async function loadActivityLog(){
  try{
    const r=await fetch('/api/activity-log');
    const d=await r.json();
    const entries=d.entries||[];
    _activityLogEntries=entries;
    const box=document.getElementById('activityLog');
    if(!box)return;
    if(!entries.length){
      box.innerHTML='<div style="color:var(--muted)">Keine Aktivitaet vorhanden</div>';
      document.getElementById('logStats').textContent='0 Eintraege';
      return;
    }
    let html='';
    for(const e of entries){
      let cls='log-line';
      let icon='';
      if(e.level==='ok'||String(e.msg).includes('OK=true')){cls='log-line-ok';icon='✓ ';}
      else if(e.level==='err'||String(e.msg).includes('Error')||String(e.msg).includes('Fehler')){cls='log-line-err';icon='✗ ';}
      else if(e.level==='warn'||String(e.msg).includes('WARN')){cls='log-line-warn';icon='⚠ ';}
      else if(e.level==='scraper'){cls='';icon='🔧 ';}
      else if(e.level==='cf'){cls='';icon='🍪 ';}
      const ts=e.ts?`<span style="color:var(--muted)">[${e.ts}]</span> `:'';
      html+=`<div class="${cls}">${ts}${icon}${esc(e.msg)}</div>`;
    }
    box.innerHTML=html;
    box.scrollTop=box.scrollHeight;
    document.getElementById('logStats').textContent=entries.length+' Eintraege';
  }catch(e){
    console.error('activity-log error:',e);
  }
}

function clearLog(){
  if(!confirm('Log leeren?'))return;
  fetch('/api/activity-log/clear',{method:'POST'}).then(()=>{
    _activityLogEntries=[];
    loadActivityLog();
    showToast('Log geleert','ok');
  });
}

function exportLog(){
  const text=_activityLogEntries.map(e=>{
    const ts=e.ts?`[${e.ts}] `:'';
    return ts+e.msg;
  }).join('\n');
  if(!text){showToast('Keine Eintraege zum Exportieren','err');return;}
  const blob=new Blob([text],{type:'text/plain;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download='sprachnudel_activity_log_'+new Date().toISOString().slice(0,10)+'.txt';
  a.click();URL.revokeObjectURL(url);
  showToast('Log exportiert','ok');
}

loadActivityLog();
setInterval(loadActivityLog,5000);

/* === SPRUECHEKLOPPER — verschoben auf /sprueche === */
</script>
</body>
</html>"""

SPRUECHE_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>Reime + Sprueche</title>
  <link rel="stylesheet" href="/static/style.css">
  <style>
    :root {
      --bg: #0f0f17; --surface: #1a1a26; --surface2: #232334;
      --border: #2e2e44; --text: #e7e7f0; --muted: #8a8aa3;
      --accent: #ff6b6b; --accent2: #7ad7f0; --ok: #9be29b; --warn: #ffd166;
    }
    * { box-sizing: border-box; }
    body { margin:0; padding:0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
           background: var(--bg); color: var(--text); }
    header { background: linear-gradient(135deg, #1a1a26 0%, #2a1a26 100%);
             padding: 16px 24px; border-bottom: 1px solid var(--border);
             display: flex; align-items: center; gap: 16px; }
    header h1 { margin:0; font-size:1.2rem; color: var(--accent); }
    header .nav a { color: var(--muted); text-decoration: none; margin-left: 16px; font-size:.85rem; }
    header .nav a:hover { color: var(--accent2); }
    header .nav a.active { color: var(--accent); font-weight: 600; }
    main { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
    .card { background: var(--surface); border: 1px solid var(--border);
            border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    .card h2 { margin: 0 0 12px; font-size: 1rem; color: var(--accent);
               display: flex; align-items: center; gap: 8px; }
    .search-bar { display: flex; gap: 8px; margin-bottom: 12px; }
    .search-bar input { flex: 1; padding: 10px 14px; font-size: 1rem;
                        background: var(--surface2); color: var(--text);
                        border: 1px solid var(--border); border-radius: 4px; }
    .search-bar input:focus { outline: none; border-color: var(--accent); }
    .btn { padding: 8px 16px; background: var(--surface2); color: var(--text);
           border: 1px solid var(--border); border-radius: 4px; cursor: pointer; font-size: .9rem; }
    .btn:hover { background: var(--border); }
    .btn-primary { background: linear-gradient(135deg, #ff6b6b, #ee5a6f);
                   color: #fff; border: none; font-weight: 600; }
    .btn-primary:hover { filter: brightness(1.1); }
    .btn-test { background: #2a2a3d; }
    .reim-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
                 gap: 8px; max-height: 400px; overflow-y: auto; }
    .reim-tile { background: var(--surface2); border: 1px solid var(--border);
                 border-radius: 4px; padding: 8px 10px; cursor: pointer;
                 transition: all .15s; }
    .reim-tile:hover { border-color: var(--accent); transform: translateY(-1px); }
    .reim-tile .wort { font-size: 1rem; font-weight: 600; color: var(--accent2); }
    .reim-tile .meta { font-size: .7rem; color: var(--muted); margin-top: 2px; }
    .reim-tile .score { display: inline-block; padding: 1px 6px; border-radius: 3px;
                        font-size: .7rem; margin-right: 4px; }
    .empty { color: var(--muted); padding: 16px; text-align: center; font-size: .9rem; }
    .loading { text-align: center; padding: 20px; color: var(--muted); }
    .loading::after { content: "..."; animation: dots 1.5s steps(3) infinite; }
    @keyframes dots { 0%, 33% { content: "."; } 66% { content: ".."; } 100% { content: "..."; } }
    .gen-grid { display: grid; grid-template-columns: 1fr 1fr 1fr auto;
                gap: 8px; align-items: end; margin-bottom: 12px; }
    .gen-grid label { font-size: .7rem; color: var(--muted); display: block; margin-bottom: 2px; }
    .gen-grid select { width: 100%; padding: 6px; background: var(--surface2);
                       color: var(--text); border: 1px solid var(--border); border-radius: 4px; }
    .gen-result { background: var(--surface2); border: 1px solid var(--border);
                  border-radius: 6px; padding: 14px; min-height: 80px;
                  white-space: pre-wrap; font-family: Georgia, serif;
                  font-size: 1rem; line-height: 1.7; }
    .gen-meta { display: flex; gap: 12px; margin-top: 8px;
                font-size: .7rem; color: var(--muted); flex-wrap: wrap; }
    .gen-history { margin-top: 10px; max-height: 200px; overflow-y: auto;
                   background: var(--surface2); border: 1px solid var(--border);
                   border-radius: 4px; padding: 8px; font-size: .8rem; }
    .gen-history > div { padding: 6px 0; border-bottom: 1px solid var(--border); }
    .toast { position: fixed; top: 16px; right: 16px; padding: 10px 16px;
             border-radius: 4px; color: #fff; z-index: 999;
             animation: slideIn .2s ease-out; }
    .toast.ok { background: #2d6a4f; }
    .toast.err { background: #9b2226; }
    @keyframes slideIn { from { transform: translateX(20px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    .reim-suggestions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
    .reim-suggestions span { padding: 2px 8px; background: var(--surface2);
                             border: 1px solid var(--border); border-radius: 12px;
                             font-size: .75rem; cursor: pointer; }
    .reim-suggestions span:hover { border-color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <h1>🪕 Reime + Sprüche</h1>
    <nav class="nav">
      <a href="/">🏠 Dashboard</a>
      <a href="/sprueche" class="active">🎭 Reime / Generator</a>
    </nav>
  </header>

  <main>
    <!-- Reim-Suche -->
    <div class="card">
      <h2>🔍 Reim-Suche</h2>
      <div class="search-bar">
        <input type="text" id="searchInput" placeholder="Wort eingeben (z.B. Bauer, Bier, Hof...)"
               onkeydown="if(event.key==='Enter') searchReime()">
        <button class="btn btn-primary" onclick="searchReime()">Suchen</button>
      </div>
      <div id="reimResults" class="reim-grid">
        <div class="empty">Noch nichts gesucht. Tipp ein Wort ein, um Reime zu finden.</div>
      </div>
    </div>

    <!-- SPRUECHEKLOPPER -->
    <div class="card">
      <h2>🤪 SPRUECHEKLOPPER</h2>
      <div style="font-size:.7rem;color:var(--muted);margin-bottom:8px" id="genStats">… lade</div>
      <div class="gen-grid">
        <div>
          <label>Format</label>
          <select id="genMode">
            <option value="long">4 Zeilen (AABB) – Goldstandard</option>
            <option value="short">2 Zeilen (AA) – Knapp</option>
          </select>
        </div>
        <div>
          <label>Min-Score (0–5)</label>
          <select id="genMinScore">
            <option value="3">3 – solide</option>
            <option value="4">4 – gut</option>
            <option value="5" selected>5 – Goldstandard</option>
          </select>
        </div>
        <div>
          <label>Kandidaten</label>
          <select id="genCandidates">
            <option value="2">2</option>
            <option value="3" selected>3</option>
            <option value="5">5</option>
          </select>
        </div>
        <div>
          <label>Anzahl Sprüche</label>
          <select id="genAnzahl">
            <option value="1" selected>1</option>
            <option value="3">3</option>
            <option value="5">5</option>
            <option value="10">10</option>
          </select>
        </div>
        <div>
          <label>Modell</label>
          <select id="genModel">
            <option value="">GLM-4.6 (Default)</option>
            <option value="glm-4-plus">GLM-4-Plus</option>
            <option value="glm-5-turbo">GLM-5-Turbo</option>
            <option value="glm-4.7-flashx">GLM-4.7-FlashX</option>
            <option value="glm-4.5-flash">GLM-4.5-Flash</option>
            <option value="grok-4.3">Grok-4.3 (xAI)</option>
            <option value="grok-3">Grok-3 (xAI)</option>
            <option value="grok-3-mini">Grok-3-Mini (xAI)</option>
          </select>
        </div>
        <button class="btn btn-primary" id="btnGenerate" onclick="generateSpruch()">⚡ Kloppen!</button>
      <button class="btn btn-test" id="btnCancelGen" onclick="cancelGenerate()" style="display:none;background:#ff4444;color:#fff;border-color:#ff4444">✋ Abbrechen</button>
      </div>
      <details style="margin-top:8px">
      <summary style="cursor:pointer;font-size:.8rem;color:var(--accent)">🎛️ Drehscheibe (Feinsteuerung)</summary>
      <div class="gen-grid" style="margin-top:8px">
        <div>
          <label>Figur</label>
          <select id="dsFigur">
            <option value="zufall" selected>🎲 Zufall</option>
            <optgroup label="Klassisch">
              <option>Bauer</option><option>Bäuerin</option><option>Knecht</option>
              <option>Magd</option><option>Opa</option><option>Oma</option>
              <option>Schwiegermutter</option><option>Nachbar</option><option>Nachbarin</option>
            </optgroup>
            <optgroup label="Dorf">
              <option>Pfarrer</option><option>Bürgermeister</option><option>Wirt</option>
              <option>Wirtin</option><option>Postbote</option><option>Lehrer</option>
              <option>Tierarzt</option><option>Dorfdepp</option><option>Jäger</option>
              <option>Förster</option><option>Schäfer</option>
            </optgroup>
            <optgroup label="Extern / Culture-Clash">
              <option>Stadtmensch</option><option>Tourist</option><option>Influencer</option>
              <option>Vertreter</option><option>Bio-Hof-Praktikant</option>
            </optgroup>
            <optgroup label="Tiere">
              <option>Hofhund</option><option>Scheunenkatze</option><option>Hahn</option>
              <option>Henne</option><option>Gans</option><option>Ziege</option>
              <option>Schaf</option><option>Pferd</option><option>Esel</option>
              <option>Rabe</option>
            </optgroup>
          </select>
        </div>
        <div>
          <label>Setting</label>
          <select id="dsSetting">
            <option value="zufall" selected>🎲 Zufall</option>
            <optgroup label="Hof-Szenen">
              <option>Heuernte</option><option>Schlachtfest</option><option>Schnapsbrennen</option>
              <option>Melken</option><option>Sturm überm Hof</option><option>Erntedank</option>
            </optgroup>
            <optgroup label="Dorf">
              <option>Dorffest</option><option>Kirmes</option><option>Schützenfest</option>
              <option>Stammtisch</option><option>Frühschoppen</option><option>Sonntagsmesse</option>
              <option>Beichtstuhl</option><option>Bauernmarkt</option><option>Viehmarkt</option>
              <option>Dorfhochzeit</option><option>Beerdigung</option>
            </optgroup>
            <optgroup label="Saison / Outdoor">
              <option>Pilze sammeln</option><option>Jagd am Hochsitz</option>
              <option>Eisheilige</option><option>Hundstage</option><option>Almabtrieb</option>
            </optgroup>
            <optgroup label="Culture-Clash">
              <option>WLAN auf dem Land</option><option>Lieferando findet den Hof nicht</option>
              <option>Tinder-Date des Knechts</option><option>Bauer auf TikTok</option>
              <option>ChatGPT-Beratung am Stammtisch</option>
            </optgroup>
          </select>
        </div>
        <div>
          <label>Twist</label>
          <select id="dsTwist">
            <option value="zufall" selected>🎲 Zufall</option>
            <option>Bäuerin überlistet Bauer</option>
            <option>Tier ist klüger als Mensch</option>
            <option>Pfarrer hat Geheimnis</option>
            <option>Schwiegermutter eskaliert</option>
            <option>Knecht & Magd-Romanze</option>
            <option>Opa liefert Lebensweisheit</option>
            <option>Tier-POV</option>
            <option>Dorftratsch wendet sich</option>
            <option>Stadtmensch versteht nichts</option>
            <option>Generationenkonflikt</option>
          </select>
        </div>
        <div>
          <label>Thema</label>
          <select id="dsThema">
            <option value="zufall" selected>🎲 Zufall</option>
          </select>
        </div>
        <div>
          <label>Form</label>
          <select id="dsForm">
            <option value="zufall" selected>🎲 Zufall</option>
            <option value="AABB">AABB (4-Zeiler Paarreim)</option>
            <option value="ABAB">ABAB (4-Zeiler Kreuzreim)</option>
            <option value="kurz">Kurz (2-Zeiler)</option>
          </select>
        </div>
      </div>
      </details>
      <div id="genLoading" class="loading" style="display:none">Klopfe Spruch</div>
      <div id="genLiveLog" style="display:none;max-height:200px;overflow-y:auto;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px;margin-top:8px;font-family:monospace;font-size:.7rem;line-height:1.4"></div>
      <div id="genResult" class="gen-result">Noch kein Spruch generiert. Klicke „Kloppen!" 🎩</div>
      <div id="genMeta" class="gen-meta"></div>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn btn-test" onclick="loadGenHistory()">📜 History</button>
        <button class="btn btn-test" onclick="clearGenHistory()">🗑️ History leeren</button>
        <button class="btn btn-test" id="btnNotionSync" onclick="syncNotion()">📤 Nach Notion syncen</button>
      </div>
      <div id="genHistory" class="gen-history" style="display:none"></div>
    </div>
  </main>

  <script>
    // ============== Reim-Suche ==============
    async function searchReime() {
      const q = document.getElementById('searchInput').value.trim();
      const box = document.getElementById('reimResults');
      if (!q) {
        box.innerHTML = '<div class="empty">Bitte ein Wort eingeben.</div>';
        return;
      }
      box.innerHTML = '<div class="loading">Suche Reime für "' + esc(q) + '"</div>';
      try {
        const r = await fetch('/api/sprachnudel/search?q=' + encodeURIComponent(q));
        const d = await r.json();
        const woerter = d.woerter || d.results || [];
        if (!woerter.length) {
          box.innerHTML = '<div class="empty">Keine Reime für „' + esc(q) + '" gefunden.</div>';
          return;
        }
        // Vorschlaege (top 8) als quick-pick
        const suggestions = d.suggestions || [];
        let html = '';
        if (suggestions.length) {
          html += '<div style="grid-column: 1/-1;margin-bottom:6px">';
          html += '<div style="font-size:.75rem;color:var(--muted);margin-bottom:4px">Vorschläge (klang-basiert):</div>';
          html += '<div class="reim-suggestions">';
          for (const s of suggestions) {
            html += '<span onclick="document.getElementById(\'searchInput\').value=\'' + esc(s).replace(/'/g, "\\'") + '\';searchReime()">' + esc(s) + '</span>';
          }
          html += '</div></div>';
        }
        for (const w of woerter) {
          const wort = esc(w.wort || w.suchwort || w);
          const silben = w.silben || '';
          const wortart = w.wortart || '';
          const score = w.score !== undefined ? w.score : '';
          const klang = w.klang || '';
          html += '<div class="reim-tile">'
                + '  <div class="wort">' + wort + '</div>'
                + '  <div class="meta">'
                + (silben ? silben + ' Silben' : '')
                + (silben && wortart ? ' · ' : '')
                + esc(wortart)
                + (klang ? ' · 🔊 ' + esc(klang) : '')
                + '  </div>'
                + '</div>';
        }
        box.innerHTML = html;
      } catch (e) {
        box.innerHTML = '<div class="empty">⚠️ Fehler: ' + esc(e.message) + '</div>';
      }
    }

    // ============== Generator ==============
    let _genLoading = false;
    let _genPollTimer = null;
    let _genLastLogCount = 0;
    async function loadGenStats() {
      try {
        const r = await fetch('/api/generate/history');
        const d = await r.json();
        const s = d.stats || {};
        document.getElementById('genStats').textContent =
          (s.klang_count || 0) + ' Reimgruppen · '
          + (s.total_words || 0) + ' Wörter · '
          + (s.history_count || 0) + ' History';
      } catch (e) { console.error('genStats error:', e); }
    }

    async function loadDsThemen() {
      try {
        const sel = document.getElementById('dsThema');
        if (!sel) return;
        const r = await fetch('/api/sprachnudel/topics');
        const d = await r.json();
        const topics = d.topics || [];
        for (const t of topics.slice(0, 50)) {
          const opt = document.createElement('option');
          opt.value = t.thema;
          opt.textContent = t.thema + ' (' + t.count + ')';
          sel.appendChild(opt);
        }
      } catch (e) { console.error('loadDsThemen error:', e); }
    }

    async function cancelGenerate() {
      try {
        await fetch('/api/generate/cancel', { method: 'POST' });
        _appendLog('⚠ Abbruch signalisiert...', '#ff4444');
      } catch (e) { console.error('cancel error:', e); }
    }

    function _appendLog(msg, color) {
      const box = document.getElementById('genLiveLog');
      const line = document.createElement('div');
      if (color) line.style.color = color;
      line.textContent = msg;
      box.appendChild(line);
      box.scrollTop = box.scrollHeight;
    }

    async function _pollGenStatus() {
      try {
        const r = await fetch('/api/generate/status');
        const d = await r.json();
        const st = d.status || {};
        const log = st.log || [];
        // Neue Log-Einträge anzeigen
        if (log.length > _genLastLogCount) {
          for (let i = _genLastLogCount; i < log.length; i++) {
            _appendLog('[' + log[i].ts + '] ' + log[i].msg);
          }
          _genLastLogCount = log.length;
        }
        // Prüfen ob fertig
        if (!st.running && d.result) {
          const res = d.result;
          document.getElementById('genLoading').style.display = 'none';
          document.getElementById('btnGenerate').disabled = false;
          document.getElementById('btnGenerate').textContent = '⚡ Kloppen!';
          document.getElementById('btnCancelGen').style.display = 'none';
          _genLoading = false;
          if (res.ok) {
            if (res.sprueche && Array.isArray(res.sprueche)) {
              // Batch-Modus: mehrere Sprueche anzeigen
              const html = res.sprueche.map((s, idx) => {
                const sc = s.judge_score || s.score || 0;
                const scColor = sc >= 5 ? '#ffd700' : sc >= 4 ? '#9be29b' :
                                sc >= 3 ? '#7ad7f0' : sc >= 2 ? '#ffa07a' : '#ff6b6b';
                return '<div style="border-bottom:1px solid var(--border);padding:6px 0">'
                  + '<div style="white-space:pre-wrap">' + esc(s.spruch || '(leer)') + '</div>'
                  + '<div style="font-size:.65rem;color:var(--muted);margin-top:2px">'
                  + '<span style="color:' + scColor + ';font-weight:600">⭐ ' + sc + '/5</span>'
                  + ' | 🤖 ' + esc(s.model || '?')
                  + ' | 💰 $' + ((s.kosten_usd || 0).toFixed(6))
                  + '</div></div>';
              }).join('');
              document.getElementById('genResult').innerHTML = html;
              document.getElementById('genMeta').innerHTML = '<span style="color:#9be29b;font-weight:600">📦 ' + res.count + ' Sprüche</span>';
              showToast(res.count + ' Sprüche generiert', 'ok');
            } else {
              document.getElementById('genResult').textContent = res.spruch || '(leer)';
              const score = res.score || 0;
              const scoreColor = score >= 5 ? '#ffd700' : score >= 4 ? '#9be29b' :
                                 score >= 3 ? '#7ad7f0' : score >= 2 ? '#ffa07a' : '#ff6b6b';
              document.getElementById('genMeta').innerHTML = '<span style="color:' + scoreColor + ';font-weight:600">⭐ Score: ' + score + '/5</span>'
                + '<span>🤖 ' + esc(res.model || '?') + '</span>'
                + '<span>📝 ' + esc((res.reimwoerter || []).join(' / ')) + '</span>'
                + '<span>💰 $' + ((res.kosten_usd || 0).toFixed(6)) + '</span>';
              showToast('Spruch generiert (Score ' + score + '/5)', 'ok');
            }
          } else {
            document.getElementById('genResult').textContent = '⚠️ ' + (res.error || 'Generierung fehlgeschlagen');
            _appendLog('Fehler: ' + (res.error || '?'), '#ff4444');
            showToast(res.error || 'Fehler bei Generierung', 'err');
          }
          loadGenStats();
          if (_genPollTimer) { clearInterval(_genPollTimer); _genPollTimer = null; }
          return;
        }
      } catch (e) { console.error('poll error:', e); }
    }

    async function generateSpruch() {
      if (_genLoading) return;
      _genLoading = true;
      _genLastLogCount = 0;
      const btn = document.getElementById('btnGenerate');
      const loading = document.getElementById('genLoading');
      const result = document.getElementById('genResult');
      const meta = document.getElementById('genMeta');
      const logBox = document.getElementById('genLiveLog');
      const cancelBtn = document.getElementById('btnCancelGen');
      btn.disabled = true;
      btn.textContent = '⏳ klopfe…';
      cancelBtn.style.display = 'inline-block';
      loading.style.display = 'block';
      logBox.style.display = 'block';
      logBox.innerHTML = '';
      result.textContent = '';
      meta.innerHTML = '';
      try {
        const mode = document.getElementById('genMode').value;
        const minScore = parseInt(document.getElementById('genMinScore').value);
        const candidates = parseInt(document.getElementById('genCandidates').value);
        const anzahl = parseInt(document.getElementById('genAnzahl').value);
        const model = document.getElementById('genModel').value;
        const body = { mode, min_score: minScore, candidates, anzahl };
        if (model) body.model = model;
        // ── Drehscheibe einlesen ──
        const dsFigur = document.getElementById('dsFigur');
        const dsSetting = document.getElementById('dsSetting');
        const dsTwist = document.getElementById('dsTwist');
        const dsThema = document.getElementById('dsThema');
        const dsForm = document.getElementById('dsForm');
        if (dsFigur || dsSetting || dsTwist || dsThema || dsForm) {
          const drehscheibe = {
            figur: dsFigur ? dsFigur.value : 'zufall',
            setting: dsSetting ? dsSetting.value : 'zufall',
            twist: dsTwist ? dsTwist.value : 'zufall',
            thema: dsThema ? dsThema.value : 'zufall',
            form: dsForm ? dsForm.value : 'zufall',
          };
          const hatVorgabe = Object.values(drehscheibe).some(v => v && v !== 'zufall');
          if (hatVorgabe) body.drehscheibe = drehscheibe;
        }
        const r = await fetch('/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const d = await r.json();
        if (!d.ok && d.status !== 'started') {
          result.textContent = '⚠️ ' + (d.error || 'Start fehlgeschlagen');
          btn.disabled = false;
          btn.textContent = '⚡ Kloppen!';
          cancelBtn.style.display = 'none';
          loading.style.display = 'none';
          _genLoading = false;
          return;
        }
        _appendLog('Generierung gestartet...', '#9be29b');
        _genPollTimer = setInterval(_pollGenStatus, 500);
      } catch (e) {
        result.textContent = '⚠️ Fehler: ' + e.message;
        btn.disabled = false;
        btn.textContent = '⚡ Kloppen!';
        cancelBtn.style.display = 'none';
        loading.style.display = 'none';
        _genLoading = false;
      }
    }
    async function loadGenHistory() {
      const box = document.getElementById('genHistory');
      if (box.style.display === 'block') {
        box.style.display = 'none';
        return;
      }
      try {
        const r = await fetch('/api/generate/history');
        const d = await r.json();
        const h = d.history || [];
        if (!h.length) {
          box.innerHTML = '<div class="empty">Noch keine Sprüche generiert.</div>';
        } else {
          box.innerHTML = h.map(e => {
            const score = e.score || 0;
            const scoreColor = score >= 5 ? '#ffd700' : score >= 4 ? '#9be29b' :
                               score >= 3 ? '#7ad7f0' : '#ffa07a';
            const ts = e.ts || '';
            return '<div>'
              + '<div style="display:flex;gap:8px;align-items:center;margin-bottom:2px">'
              + '<span style="color:' + scoreColor + ';font-size:.7rem">⭐ ' + score + '/5</span>'
              + '<span style="color:var(--muted);font-size:.7rem">' + esc(ts) + '</span>'
              + '<span style="color:var(--muted);font-size:.7rem">' + esc(e.klang || '') + '</span>'
              + '</div>'
              + '<div style="font-family:Georgia,serif;white-space:pre-wrap">' + esc(e.spruch || '') + '</div>'
              + '</div>';
          }).join('');
        }
        box.style.display = 'block';
      } catch (e) { showToast('History-Fehler: ' + e.message, 'err'); }
    }
    async function clearGenHistory() {
      if (!confirm('Generator-History leeren?')) return;
      try {
        await fetch('/api/generate/clear', { method: 'POST' });
        document.getElementById('genHistory').style.display = 'none';
        loadGenStats();
        showToast('History geleert', 'ok');
      } catch (e) { showToast('Fehler: ' + e.message, 'err'); }
    }
    async function syncNotion() {
      const btn = document.getElementById('btnNotionSync');
      btn.disabled = true;
      btn.textContent = '⏳ synce…';
      try {
        const r = await fetch('/api/notion/sync', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
          const msg = d.synced + ' Spruch/Sprueche gesynct'
            + (d.errors && d.errors.length ? ' (' + d.errors.length + ' Fehler)' : '');
          showToast(msg, d.errors && d.errors.length ? 'warn' : 'ok');
          if (d.errors && d.errors.length) console.error('Notion-Sync-Fehler:', d.errors);
        } else {
          showToast('Notion-Sync fehlgeschlagen: ' + (d.error || '?'), 'err');
        }
      } catch (e) {
        showToast('Notion-Sync-Fehler: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
        btn.textContent = '📤 Nach Notion syncen';
      }
    }

    // ============== Utils ==============
    function esc(s) {
      if (s === null || s === undefined) return '';
      return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }
    function showToast(msg, kind) {
      const t = document.createElement('div');
      t.className = 'toast ' + (kind || 'ok');
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(() => t.remove(), 3000);
    }

    // Init
    loadGenStats();
    setInterval(loadGenStats, 30000);
    loadDsThemen();
  </script>
</body>
</html>
"""

HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sprachnudel Reimwoerterbuch</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0c0c18;--surface:#141428;--surface2:#1c1c36;--border:#2a2a50;--accent:#7c6bff;--accent2:#ff6b9d;--green:#4ecdc4;--yellow:#e8d44d;--red:#ff5555;--blue:#5599ff;--orange:#ff9f43;--text:#e0e0f0;--muted:#777}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}

.topbar{background:var(--surface);border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;height:56px;gap:16px}
.topbar h1{font-size:1.2rem;color:var(--accent)}
.topbar h1 span{color:var(--accent2)}
.topbar .nav-links{margin-left:auto;display:flex;gap:12px;align-items:center}
.topbar .nav-links a{color:var(--muted);text-decoration:none;font-size:.85rem;padding:4px 10px;border-radius:6px;transition:all .15s}
.topbar .nav-links a:hover{color:var(--accent);background:var(--surface2)}
.topbar .nav-links a.active{color:var(--accent);font-weight:600}

.page{padding:20px 24px;flex:1;overflow-y:auto;max-width:1200px;margin:0 auto;width:100%}

.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px}
.card h2{font-size:1rem;color:var(--accent);margin-bottom:12px;display:flex;align-items:center;gap:8px}
.card h2 .badge{font-size:.7rem;padding:2px 8px;border-radius:10px;font-weight:700}
.badge-v12{background:rgba(124,107,255,.2);color:var(--accent)}
.badge-green{background:rgba(78,205,196,.2);color:var(--green)}
.badge-yellow{background:rgba(232,212,77,.2);color:var(--yellow)}

.btn{padding:8px 16px;border-radius:8px;border:1px solid var(--border);background:var(--surface2);color:var(--text);font-size:.85rem;cursor:pointer;font-weight:600;transition:all .15s;display:inline-flex;align-items:center;gap:6px}
.btn:hover{border-color:var(--accent);transform:translateY(-1px)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{background:#6b5aee}
.btn-sm{padding:4px 10px;font-size:.75rem;border-radius:6px}
.btn-active{border-color:var(--accent);background:rgba(124,107,255,.15);color:var(--accent)}

.input{padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.85rem;width:100%}
.input:focus{outline:none;border-color:var(--accent)}
select.input{cursor:pointer;min-width:120px}

.form-row{display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap}

/* Tabs */
.tab-bar{display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:0}
.tab-btn{padding:8px 18px;border:none;background:transparent;color:var(--muted);font-size:.9rem;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;font-weight:600}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}

/* Wort-Info Panel */
.wort-info{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.wort-info-main{grid-column:1/-1}
.wort-info-side{grid-column:1/-1}
@media(min-width:768px){.wort-info-side{grid-column:2}}

.wort-tag{display:inline-block;padding:2px 10px;margin:2px;border-radius:12px;font-size:.8rem;font-weight:600}
.tag-pos{background:rgba(124,107,255,.2);color:var(--accent)}
.tag-ipa{background:rgba(85,153,255,.2);color:var(--blue)}
.tag-freq{background:rgba(78,205,196,.15);color:var(--green)}
.tag-topic{background:rgba(255,107,157,.15);color:var(--accent2);cursor:pointer}
.tag-topic:hover{background:rgba(255,107,157,.3)}
.tag-syn{background:rgba(78,205,196,.15);color:var(--green);cursor:pointer}
.tag-syn:hover{background:rgba(78,205,196,.3)}
.tag-ant{background:rgba(255,85,85,.15);color:var(--red);cursor:pointer}
.tag-ant:hover{background:rgba(255,85,85,.3)}
.tag-rel{background:rgba(255,159,67,.15);color:var(--orange);cursor:pointer}
.tag-rel:hover{background:rgba(255,159,67,.3)}
.tag-def{background:var(--surface2);color:var(--text);border:1px solid var(--border);display:block;margin:3px 0;padding:6px 10px;border-radius:6px;font-size:.85rem}

/* Themed Rhymes */
.themed-group{margin-bottom:12px}
.themed-group-title{font-size:.85rem;font-weight:700;color:var(--accent2);margin-bottom:6px;padding:4px 8px;background:rgba(255,107,157,.1);border-radius:6px;display:inline-block}

/* Reim-Karten */
.sn-wortart{margin-bottom:16px}
.sn-wortart-title{font-size:1rem;font-weight:700;color:var(--accent);margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}
.sn-silben-group{margin-bottom:10px;margin-left:12px}
.sn-silben-label{font-size:.8rem;color:var(--muted);margin-bottom:4px;font-weight:600}
.sn-word{display:inline-flex;align-items:center;gap:4px;padding:4px 12px;margin:3px;border-radius:6px;background:var(--surface2);border:1px solid var(--border);font-size:.9rem;cursor:pointer;transition:all .15s}
.sn-word:hover{border-color:var(--accent);background:rgba(124,107,255,.1)}
.sn-word .sem-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sem-high{background:var(--green)}
.sem-mid{background:var(--yellow)}
.sem-low{background:var(--muted)}

.sn-total{font-size:1.1rem;color:var(--green);font-weight:700;margin-bottom:16px}
.sn-empty{text-align:center;padding:40px;color:var(--muted);font-size:1rem}
.sn-note{font-size:.8rem;color:var(--yellow);margin-top:8px}

/* Semantik-Score Bar */
.sem-bar{display:inline-block;width:40px;height:6px;background:var(--bg);border-radius:3px;overflow:hidden;vertical-align:middle;margin-left:4px}
.sem-bar-fill{height:100%;border-radius:3px;transition:width .3s}

/* Themen-Suche */
.topic-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}
.topic-card{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px 14px;cursor:pointer;transition:all .15s}
.topic-card:hover{border-color:var(--accent2);transform:translateY(-1px)}
.topic-card .topic-name{font-weight:600;font-size:.9rem;color:var(--accent2)}
.topic-card .topic-count{font-size:.75rem;color:var(--muted)}

/* Filter */
.filter-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.filter-chip{padding:4px 12px;border-radius:12px;font-size:.8rem;cursor:pointer;border:1px solid var(--border);background:var(--surface2);color:var(--muted);transition:all .15s;font-weight:600}
.filter-chip:hover{border-color:var(--accent);color:var(--text)}
.filter-chip.active{border-color:var(--accent);background:rgba(124,107,255,.15);color:var(--accent)}

.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}

.section-label{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;font-weight:700}
</style>
</head>
<body>

<div class="topbar">
  <h1>Sprach<span>nudel</span> Reimwoerterbuch</h1>
  <div class="nav-links">
    <a href="/" class="active">Reimsuche</a>
    <a href="/sprueche">SPRUECHEKLOPPER</a>
    <a href="/scrape-monitor">Monitor</a>
  </div>
</div>

<div class="page">
  <!-- Such-Tabs -->
  <div class="card">
    <div class="tab-bar">
      <button class="tab-btn active" onclick="snSwitchTab('word')">Wort-Suche</button>
      <button class="tab-btn" onclick="snSwitchTab('topic')">Themen-Suche</button>
    </div>

    <!-- TAB: Wort-Suche -->
    <div id="tabWord" class="tab-content active">
      <div class="form-row">
        <input class="input" id="snSearch" placeholder="Wort eingeben und Reime finden..." style="font-size:1.1rem;padding:12px 16px" onkeydown="if(event.key==='Enter')snDoSearch()">
        <select class="input" id="snWortartFilter" style="max-width:180px" onchange="snDoSearch()">
          <option value="">Alle Wortarten</option>
          <option value="substantiv">Substantiv</option>
          <option value="verb">Verb</option>
          <option value="adjektiv">Adjektiv</option>
          <option value="adjektive">Adjektive (SN)</option>
          <option value="substantive">Substantive (SN)</option>
          <option value="verben">Verben (SN)</option>
        </select>
        <button class="btn btn-primary" onclick="snDoSearch()" style="padding:12px 24px;font-size:1rem">Suchen</button>
      </div>
    </div>

    <!-- TAB: Themen-Suche -->
    <div id="tabTopic" class="tab-content">
      <div class="form-row">
        <input class="input" id="topicSearch" placeholder="Thema eingeben (z.B. Natur, Essen, Gefuehl...)" style="font-size:1.1rem;padding:12px 16px" onkeydown="if(event.key==='Enter')snTopicSearch()">
        <button class="btn btn-primary" onclick="snTopicSearch()" style="padding:12px 24px;font-size:1rem">Themen-Suche</button>
        <button class="btn" onclick="snLoadTopics()" style="padding:12px 16px">Alle Themen</button>
      </div>
      <div id="topicResults"></div>
    </div>

    <div id="snStats" style="font-size:.85rem;color:var(--muted);margin-top:8px"></div>
  </div>

  <!-- Wort-Info Panel (Wortart, IPA, Synonyme, etc.) -->
  <div id="snWordInfo"></div>

  <!-- Thematisch gruppierte Reime -->
  <div id="snThemedRhymes"></div>

  <!-- Reim-Ergebnisse -->
  <div id="snResults"></div>
</div>

<script>
function snE(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}

function snSetSearchWord(w){document.getElementById('snSearch').value=w;snSwitchTab('word');snDoSearch()}

function snSwitchTab(tab){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.getElementById('tabWord').classList.toggle('active',tab==='word');
  document.getElementById('tabTopic').classList.toggle('active',tab==='topic');
  document.querySelectorAll('.tab-btn').forEach(b=>{
    if(b.textContent.includes('Wort')&&tab==='word')b.classList.add('active');
    if(b.textContent.includes('Themen')&&tab==='topic')b.classList.add('active');
  });
}

let _snTimer=null;
function snDoSearch(){
  clearTimeout(_snTimer);
  _snTimer=setTimeout(_snDoSearch,200);
}

function semDotColor(score){
  if(!score||score<=0)return'';
  if(score>=0.5)return'sem-high';
  if(score>=0.2)return'sem-mid';
  return'sem-low';
}

function semBar(score){
  if(!score||score<=0)return'';
  const pct=Math.min(100,score*100);
  const color=score>=0.5?'var(--green)':score>=0.2?'var(--yellow)':'var(--muted)';
  return'<span class="sem-bar"><span class="sem-bar-fill" style="width:'+pct+'%;background:'+color+'"></span></span>';
}

function freqLabel(f){
  if(!f||f>=100)return'';
  if(f<=10)return'<span class="wort-tag tag-freq">Haeufig</span>';
  if(f<=50)return'<span class="wort-tag tag-freq">Gebräuchlich</span>';
  return'';
}

async function _snDoSearch(){
  const q=document.getElementById('snSearch').value.trim();
  if(!q)return;
  const waFilter=document.getElementById('snWortartFilter').value;
  document.getElementById('snStats').innerHTML='<span class="spinner"></span> Suche...';
  document.getElementById('snResults').innerHTML='';
  document.getElementById('snWordInfo').innerHTML='';
  document.getElementById('snThemedRhymes').innerHTML='';
  try{
    let url='/api/sprachnudel/search?q='+encodeURIComponent(q);
    if(waFilter)url+='&wortart='+encodeURIComponent(waFilter);
    const r=await fetch(url);
    const d=await r.json();
    if(d.error){document.getElementById('snStats').textContent=d.error;return}

    const total=d.total_filtered||d.total||0;
    const suchwort=d.suchwort||q;
    const isV12=d.cached==='v12_semantic';
    document.getElementById('snStats').innerHTML='Für <strong style="color:var(--accent)">'+snE(suchwort)+'</strong> wurden <strong style="color:var(--green)">'+total+'</strong> Reimwörter gefunden. '
      +(isV12?'<span class="wort-tag tag-pos">v12 Semantik</span>':'')
      +(d.haeufigkeit&&d.haeufigkeit<100?'<span class="wort-tag tag-freq">Häufigkeit: '+d.haeufigkeit+'</span>':'');

    if(total===0&&!(d.synonyme&&d.synonyme.length)){
      document.getElementById('snResults').innerHTML='<div class="sn-empty">Keine Reime gefunden</div>';
      if(d.note)document.getElementById('snResults').innerHTML+='<div class="sn-note">'+snE(d.note)+'</div>';
      return;
    }

    // ── Wort-Info Panel ──
    if(isV12){
      let info='<div class="card"><h2>Wort-Info: '+snE(suchwort)+'</h2>';
      info+='<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">';
      // Wortart
      if(d.wortart){
        const pos=Array.isArray(d.wortart)?d.wortart:[d.wortart];
        for(const p of pos)info+='<span class="wort-tag tag-pos">'+snE(p)+'</span>';
      }
      // IPA
      if(d.ipa){
        const ips=Array.isArray(d.ipa)?d.ipa:[d.ipa];
        for(const ip of ips)info+='<span class="wort-tag tag-ipa">/'+snE(ip)+'/</span>';
      }
      info+=freqLabel(d.haeufigkeit);
      info+='</div>';

      // Definitionen
      if(d.definitionen&&d.definitionen.length){
        info+='<div class="section-label">Definitionen</div>';
        for(const def of d.definitionen.slice(0,5))info+='<div class="tag-def">'+snE(def)+'</div>';
      }

      // Synonyme
      if(d.synonyme&&d.synonyme.length){
        info+='<div class="section-label" style="margin-top:12px">Ähnliche Wörter (Synonyme)</div><div style="display:flex;flex-wrap:wrap;gap:4px">';
        for(const s of d.synonyme.slice(0,20))info+='<span class="wort-tag tag-syn" data-search="'+snE(s)+'">'+snE(s)+'</span>';
        info+='</div>';
      }

      // Antonyme
      if(d.antonyme&&d.antonyme.length){
        info+='<div class="section-label" style="margin-top:12px">Gegenteile (Antonyme)</div><div style="display:flex;flex-wrap:wrap;gap:4px">';
        for(const a of d.antonyme.slice(0,10))info+='<span class="wort-tag tag-ant" data-search="'+snE(a)+'">'+snE(a)+'</span>';
        info+='</div>';
      }

      // Verwandte Begriffe
      if(d.verwandte&&d.verwandte.length){
        info+='<div class="section-label" style="margin-top:12px">Verwandte Begriffe</div><div style="display:flex;flex-wrap:wrap;gap:4px">';
        for(const v of d.verwandte.slice(0,15))info+='<span class="wort-tag tag-rel" data-search="'+snE(v)+'">'+snE(v)+'</span>';
        info+='</div>';
      }

      // Abgeleitete
      if(d.abgeleitete&&d.abgeleitete.length){
        info+='<div class="section-label" style="margin-top:12px">Abgeleitete Wörter</div><div style="display:flex;flex-wrap:wrap;gap:4px">';
        for(const ab of d.abgeleitete.slice(0,10))info+='<span class="wort-tag tag-rel" data-search="'+snE(ab)+'">'+snE(ab)+'</span>';
        info+='</div>';
      }

      // Themen
      if(d.themen&&d.themen.length){
        info+='<div class="section-label" style="margin-top:12px">Semantische Felder / Themen</div><div style="display:flex;flex-wrap:wrap;gap:4px">';
        for(const t of d.themen.slice(0,15))info+='<span class="wort-tag tag-topic" data-topic="'+snE(t)+'">'+snE(t)+'</span>';
        info+='</div>';
      }

      info+='</div>';
      document.getElementById('snWordInfo').innerHTML=info;
    }

    // ── Thematisch gruppierte Reime ──
    if(d.themed_rhymes&&d.themed_rhymes.length){
      let thtml='<div class="card"><h2>Themen-Gruppierte Reime <span class="badge badge-v12">Semantik</span></h2>';
      for(const tg of d.themed_rhymes){
        if(!tg.woerter||!tg.woerter.length)continue;
        const isOther=tg.thema==='Weitere';
        thtml+='<div class="themed-group">';
        thtml+='<span class="themed-group-title">'+(isOther?'Weitere':'🎯 '+snE(tg.thema))+'</span>';
        thtml+='<div style="margin-top:4px">';
        for(const w of tg.woerter.slice(0,30)){
          thtml+='<span class="sn-word" data-word="'+snE(w)+'">'+snE(w)+'</span>';
        }
        thtml+='</div></div>';
      }
      thtml+='</div>';
      document.getElementById('snThemedRhymes').innerHTML=thtml;
    }

    // ── Reim-Ergebnisse nach Kategorie ──
    let html='';
    const kategorien=d.kategorien||[];

    // Build semantic score lookup from rhymes
    const semLookup={};
    if(d.rhymes){
      for(const r of (d.rhymes||[])){
        const rw=(r.wort||'').trim();
        if(r.semantik_score)semLookup[rw.trim().toLowerCase()]={score:r.semantik_score,gruende:r.semantik_gruende||[]};
      }
    }

    for(const kat of kategorien){
      const wa=kat.wortart||'';
      const label=wa.charAt(0).toUpperCase()+wa.slice(1);
      html+='<div class="sn-wortart">';
      html+='<div class="sn-wortart-title">'+snE(label)+'</div>';
      for(const grp of(kat.gruppen||[])){
        const silben=grp.silben||'?';
        const silbenLabel=silben===1?'Mit einer Silbe':'Mit '+silben+' Silben';
        html+='<div class="sn-silben-group">';
        html+='<div class="sn-silben-label">'+snE(silbenLabel)+'</div>';
        for(const w of(grp.woerter||[])){
          const sem=semLookup[w.trim().toLowerCase()];
          const dotCls=sem?semDotColor(sem.score):'';
          html+='<span class="sn-word" data-word="'+snE(w)+'">';
          if(dotCls)html+='<span class="sem-dot '+dotCls+'" title="Semantik: '+sem.score.toFixed(2)+(sem.gruende.length?' ('+sem.gruende.join(', ')+')':'')+'"></span>';
          html+=snE(w);
          if(sem&&sem.score>0)html+=semBar(sem.score);
          html+='</span>';
        }
        html+='</div>';
      }
      html+='</div>';
    }
    document.getElementById('snResults').innerHTML=html;
  }catch(e){
    document.getElementById('snStats').textContent='Fehler: '+e.message;
  }
}

// ── Themen-Suche ──
function snTopicClick(topic){
  document.getElementById('topicSearch').value=topic;
  snSwitchTab('topic');
  snTopicSearch();
}

async function snTopicSearch(){
  const q=document.getElementById('topicSearch').value.trim();
  if(!q)return;
  const box=document.getElementById('topicResults');
  box.innerHTML='<span class="spinner"></span> Suche Themen...';
  try{
    const r=await fetch('/api/sprachnudel/topic-search?q='+encodeURIComponent(q));
    const d=await r.json();
    if(!d.ok){box.innerHTML='Fehler: '+snE(d.error);return}

    let html='';
    // Gefundene Themen
    if(d.themen&&d.themen.length){
      html+='<div style="margin-bottom:12px"><div class="section-label">Gefundene Themen ('+d.total_themen+')</div><div style="display:flex;flex-wrap:wrap;gap:6px">';
      for(const t of d.themen.slice(0,20)){
        html+='<span class="wort-tag tag-topic" data-topic="'+snE(t.thema)+'">'+snE(t.thema)+' <span style="opacity:.6">('+t.count+')</span></span>';
      }
      html+='</div></div>';
    }

    // Wörter mit Reimen
    if(d.woerter&&d.woerter.length){
      html+='<div class="section-label">Wörter mit Reimen ('+d.total_woerter+')</div>';
      html+='<div class="topic-grid">';
      for(const w of d.woerter.slice(0,50)){
        const pos=w.wortart?(Array.isArray(w.wortart)?w.wortart[0]:w.wortart):'';
        html+='<div class="topic-card" data-search="'+snE(w.wort)+'">';
        html+='<div class="topic-name">'+snE(w.wort)+'</div>';
        html+='<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">';
        html+='<span style="font-size:.75rem;color:var(--green)">'+w.reim_count+' Reime</span>';
        if(pos)html+='<span class="wort-tag tag-pos" style="font-size:.7rem">'+snE(pos)+'</span>';
        html+='</div>';
        if(w.themen&&w.themen.length){
          html+='<div style="margin-top:4px;font-size:.7rem;color:var(--muted)">'+snE(w.themen.slice(0,3).join(', '))+'</div>';
        }
        html+='</div>';
      }
      html+='</div>';
    }

    if(!d.themen.length&&!d.woerter.length){
      html='<div class="sn-empty">Keine Themen gefunden für „'+snE(q)+'"</div>';
    }
    box.innerHTML=html;
  }catch(e){
    box.innerHTML='Fehler: '+snE(e.message);
  }
}

async function snLoadTopics(){
  const box=document.getElementById('topicResults');
  box.innerHTML='<span class="spinner"></span> Lade Themen...';
  try{
    const r=await fetch('/api/sprachnudel/topics');
    const d=await r.json();
    if(!d.ok){box.innerHTML='Fehler';return}
    let html='<div class="section-label">Alle '+d.total+' Themen</div><div class="topic-grid">';
    for(const t of d.topics.slice(0,100)){
      html+='<div class="topic-card" data-topic="'+snE(t.thema)+'">';
      html+='<div class="topic-name">'+snE(t.thema)+'</div>';
      html+='<div class="topic-count">'+t.count+' Wörter</div>';
      html+='</div>';
    }
    html+='</div>';
    if(d.topics.length>100)html+='<div style="text-align:center;color:var(--muted);padding:12px;font-size:.85rem">Zeige Top 100 von '+d.total+' Themen</div>';
    box.innerHTML=html;
  }catch(e){
    box.innerHTML='Fehler: '+snE(e.message);
  }
}

// ── Zentrale Event-Delegation (sicher statt inline onclick) ──
document.addEventListener('click',function(e){
  const el=e.target.closest('[data-search]');
  if(el){snSetSearchWord(el.getAttribute('data-search'));return}
  const tp=e.target.closest('[data-topic]');
  if(tp){snTopicClick(tp.getAttribute('data-topic'));return}
  const wd=e.target.closest('.sn-word[data-word]');
  if(wd){snSetSearchWord(wd.getAttribute('data-word'));return}
});
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/sprueche")
def sprueche_page():
    """Eigene Seite: Reim-Suche + SPRUECHEKLOPPER."""
    return render_template_string(SPRUECHE_HTML)


# === Reim-Suche / Generator API =========================================
@app.route("/api/sprachnudel/search")
def api_sprachnudel_search():
    raw_q = (request.args.get("q") or "").strip()
    if not raw_q:
        return jsonify({"error": "Query fehlt"}), 400

    q = raw_q.lower()
    wortart_filter = (request.args.get("wortart") or "").strip().lower()

    # v12 Suche mit Semantik-Daten
    v12_entry = _get_v12_word(q)
    if v12_entry is not None:
        result = {
            "suchwort": v12_entry.get("suchwort", q),
            "total": v12_entry.get("reim_count", 0),
            "kategorien": v12_entry.get("kategorien", []),
            "cached": "v12_semantic",
            "_cache_version": SN_CACHE_VERSION,
            # Semantische Felder
            "wortart": v12_entry.get("wortart"),
            "ipa": v12_entry.get("ipa"),
            "definitionen": v12_entry.get("definitionen", []),
            "synonyme": v12_entry.get("synonyme", []),
            "antonyme": v12_entry.get("antonyme", []),
            "verwandte": v12_entry.get("verwandte", []),
            "abgeleitete": v12_entry.get("abgeleitete", []),
            "themen": v12_entry.get("themen", []),
            "themed_rhymes": v12_entry.get("themed_rhymes", []),
            "haeufigkeit": v12_entry.get("haeufigkeit"),
            "rhymes": v12_entry.get("rhymes", []),
        }
        # Wortart-Filter anwenden (exaktes Matching)
        if wortart_filter:
            # Mapping: User-FE-Optionen → moegliche Wortart-Werte
            _WA_MAP = {
                "substantiv": {"substantiv", "substantive"},
                "verb": {"verb", "verben"},
                "adjektiv": {"adjektiv", "adjektive"},
                "adjektive": {"adjektiv", "adjektive"},
                "substantive": {"substantiv", "substantive"},
                "verben": {"verb", "verben"},
            }
            allowed = _WA_MAP.get(wortart_filter, {wortart_filter})
            filtered_kats = []
            for kat in result["kategorien"]:
                wa = (kat.get("wortart") or "").lower()
                if wa in allowed:
                    filtered_kats.append(kat)
            result["kategorien"] = filtered_kats
            result["total_filtered"] = sum(
                sum(len(g.get("woerter", [])) for g in kat.get("gruppen", []))
                for kat in filtered_kats
            )
        return jsonify(result)

    # Fallback auf alte JSONL-Suche
    raw_result = _sn_search_raw(q)
    if raw_result is not None:
        return jsonify(raw_result)

    _sn_ensure_snapshot()
    return jsonify({
        "suchwort": q,
        "total": 0,
        "kategorien": [],
        "cached": "snapshot",
        "_cache_version": SN_CACHE_VERSION,
        "note": "Wort nicht in der gescrapten Datei vorhanden",
    })


@app.route("/api/sprachnudel/topic-search")
def api_topic_search():
    """Themen-basierte Suche: Finde Woerter nach semantischem Feld."""
    topic = (request.args.get("q") or "").strip().lower()
    if not topic:
        return jsonify({"error": "Query fehlt"}), 400

    if not _load_v12():
        return jsonify({"error": "v12 Export nicht geladen"}), 500

    # Themen durchsuchen (Teilstring-Match)
    matches = []
    for t, words in (_v12_topic_index or {}).items():
        if topic in t.lower():
            matches.append({"thema": t, "count": len(words)})

    # Top-Themen zurueckgeben
    matches.sort(key=lambda x: -x["count"])

    # Woerter fuer die Top-Themen laden
    results = []
    seen = set()
    for m in matches[:5]:  # Max 5 Themen
        thema = m["thema"]
        for word_key in (_v12_topic_index or {}).get(thema, []):
            if word_key in seen:
                continue
            seen.add(word_key)
            entry = _v12_index.get(word_key, {}) if _v12_index else {}
            if entry.get("hat_reime"):
                results.append({
                    "wort": entry.get("suchwort", word_key),
                    "reim_count": entry.get("reim_count", 0),
                    "wortart": entry.get("wortart"),
                    "themen": entry.get("themen", []),
                    "haeufigkeit": entry.get("haeufigkeit"),
                })
    results.sort(key=lambda x: (x.get("haeufigkeit") or 999, -x.get("reim_count", 0)))

    return jsonify({
        "ok": True,
        "query": topic,
        "themen": matches[:20],
        "woerter": results[:100],
        "total_themen": len(matches),
        "total_woerter": len(results),
    })


@app.route("/api/sprachnudel/topics")
def api_topics_list():
    """Listet alle verfuegbaren Themen auf."""
    if not _load_v12():
        return jsonify({"error": "v12 Export nicht geladen"}), 500

    topics = [
        {"thema": t, "count": len(words)}
        for t, words in (_v12_topic_index or {}).items()
    ]
    topics.sort(key=lambda x: -x["count"])
    return jsonify({"ok": True, "topics": topics, "total": len(topics)})


@app.route("/scrape-monitor")
def scrape_monitor():
    return render_template_string(MONITOR_HTML)


def _read_rescrape_status() -> dict:
    import os

    def _is_active(ctrl_path):
        if not ctrl_path.exists():
            return False
        try:
            with open(ctrl_path, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("status", "") not in ("done", "idle")
        except Exception:
            return False

    sitemap_ctrl = OUTPUT_DIR / "sn_sitemap_control.json"
    snowball_ctrl = SNOWBALL_CONTROL_FILE
    bauernspruch_ctrl = OUTPUT_DIR / "sn_bauernspruch_control.json"
    derewo_ctrl = OUTPUT_DIR / "sn_derewo_control.json"
    rescrape_v2_ctrl = OUTPUT_DIR / "sn_rescrape_control.json"

    sitemap_active = _is_active(sitemap_ctrl)
    snowball_active = _is_active(snowball_ctrl)
    bauernspruch_active = _is_active(bauernspruch_ctrl)
    derewo_active = _is_active(derewo_ctrl)
    rescrape_v2_active = _is_active(rescrape_v2_ctrl)
    countzero_active = _is_active(OUTPUT_DIR / "sn_countzero_control.json") or (OUTPUT_DIR / "count_zero_words.json").exists()

    use_rescrape_v2 = rescrape_v2_active
    use_snowball = not use_rescrape_v2 and snowball_active
    use_bauernspruch = not use_rescrape_v2 and not use_snowball and bauernspruch_active
    use_derewo = not use_rescrape_v2 and not use_snowball and not use_bauernspruch and derewo_active
    use_countzero = not use_rescrape_v2 and not use_snowball and not use_bauernspruch and not use_derewo and (countzero_active or (OUTPUT_DIR / "count_zero_words.json").exists())
    use_sitemap = not use_rescrape_v2 and not use_countzero and not use_snowball and not use_bauernspruch and not use_derewo and (sitemap_active or (OUTPUT_DIR / "missing_sitemap_words.json").exists())
    if use_rescrape_v2:
        total = 48094
        try:
            with open(RESCRAPE_V2_WORDS_FILE, "r", encoding="utf-8") as f:
                total = len(json.load(f))
        except Exception:
            pass
        mode = "rescrape_v2"
    elif use_snowball:
        total = 0
        queue_remaining = 0
        try:
            with open(SNOWBALL_FRONTIER_FILE, "r", encoding="utf-8") as ff:
                fd = json.load(ff)
            queue_remaining = len(fd.get("queue", []))
        except Exception:
            pass
        completed = 0
        prog_file = SNOWBALL_PROGRESS_FILE
        if prog_file.exists():
            try:
                with open(prog_file, "r", encoding="utf-8") as f:
                    prog = json.load(f)
                completed = int(prog.get("completed", 0))
            except Exception:
                pass
        total = completed + queue_remaining
        if total < completed:
            total = completed
        mode = "snowball"
    elif use_bauernspruch:
        total = 1100
        try:
            with open(BAUERNSPRUCH_WORDS_FILE, "r", encoding="utf-8") as f:
                total = len(json.load(f))
        except Exception:
            pass
        mode = "bauernspruch"
    elif use_derewo:
        total = 13200
        try:
            with open(DEREWO_WORDS_FILE, "r", encoding="utf-8") as f:
                total = len(json.load(f))
        except Exception:
            pass
        mode = "derewo"
    elif use_sitemap:
        total = 5343
        try:
            words = json.load(open(OUTPUT_DIR / "missing_sitemap_words.json", encoding="utf-8"))
            total = len(words)
        except Exception:
            pass
        mode = "sitemap"
    elif use_countzero:
        total = 48534
        try:
            words = json.load(open(OUTPUT_DIR / "count_zero_words.json", encoding="utf-8"))
            total = len(words)
        except Exception:
            pass
        mode = "countzero"
    else:
        total = 20977
        mode = "rescrape"
    result = {"total": total, "completed": 0, "fixed": 0, "still_zero": 0, "rate": 0, "eta_seconds": 0, "latest_fixed": [], "mode": mode, "snowball_added": 0, "queue_remaining": 0, "errors": 0, "blocked": 0, "cookie_refreshes": 0, "elapsed_seconds": 0, "empty": 0, "found": 0, "error_rate": 0.0}
    if use_rescrape_v2:
        prog_file = RESCRAPE_V2_PROGRESS_FILE
        patch_file = RESCRAPE_V2_PATCH_FILE
    elif use_snowball:
        prog_file = SNOWBALL_PROGRESS_FILE
        patch_file = SNOWBALL_PATCH_FILE
    elif use_bauernspruch:
        prog_file = BAUERNSPRUCH_PROGRESS_FILE
        patch_file = BAUERNSPRUCH_PATCH_FILE
    elif use_derewo:
        prog_file = DEREWO_PROGRESS_FILE
        patch_file = DEREWO_PATCH_FILE
    elif use_sitemap:
        prog_file = SITEMAP_PROGRESS_FILE
        patch_file = SITEMAP_PATCH_FILE
    elif use_countzero:
        prog_file = COUNTZERO_PROGRESS_FILE
        patch_file = COUNTZERO_PATCH_FILE
    else:
        prog_file = RESCRAPE_PROGRESS_FILE
        patch_file = RESCRAPE_PATCH_FILE
    try:
        if prog_file.exists():
            with open(prog_file, "r", encoding="utf-8") as f:
                prog = json.load(f)
            raw_completed = prog.get("completed", [])
            if isinstance(raw_completed, list):
                result["completed"] = len(raw_completed)
            else:
                result["completed"] = int(raw_completed)
            result["errors"] = int(prog.get("errors", 0))
            result["blocked"] = int(prog.get("blocked", 0))
            result["cookie_refreshes"] = int(prog.get("cookie_refreshes") or 0)
            result["empty"] = int(prog.get("empty", 0))
            result["found"] = int(prog.get("found", 0))
            if result["completed"] > 0:
                result["error_rate"] = round((result["errors"] + result["blocked"]) / result["completed"] * 100, 2)
            started = float(prog.get("started_at", 0))
            run_started = float(prog.get("run_started_at", started))
            run_completed_base = int(prog.get("run_completed_base", 0))
            if mode == "snowball":
                result["snowball_added"] = int(prog.get("snowball_added", 0))
                try:
                    with open(SNOWBALL_FRONTIER_FILE, "r", encoding="utf-8") as ff:
                        fd = json.load(ff)
                    result["queue_remaining"] = len(fd.get("queue", []))
                except Exception:
                    pass
            effective_started = run_started if run_started > 0 else started
            effective_completed = max(0, result["completed"] - run_completed_base)
            if effective_started > 0 and effective_completed > 0:
                elapsed = max(time.time() - effective_started, 1)
                result["rate"] = round(effective_completed / elapsed, 2)
                remaining = total - result["completed"]
                if result["rate"] > 0 and remaining > 0:
                    result["eta_seconds"] = int(remaining / result["rate"])
            if effective_started > 0:
                result["elapsed_seconds"] = int(time.time() - effective_started)
            history = prog.get("history", [])
            now = time.time()
            five_min_ago = now - 300
            old_snap = None
            for h in history:
                if h.get("ts", 0) <= five_min_ago:
                    old_snap = h
            if old_snap is None and len(history) >= 2:
                old_snap = history[0]
            if old_snap:
                dt = now - old_snap.get("ts", now)
                if dt > 0:
                    dc = result["completed"] - (old_snap.get("completed") or 0)
                    df = result["found"] - (old_snap.get("found") or 0)
                    de = result["still_empty"] - (old_snap.get("still_empty") or 0)
                    db = result["blocked"] - (old_snap.get("blocked") or 0)
                    derr = result["errors"] - (old_snap.get("errors") or 0)
                    result["delta_5min"] = {
                        "completed": dc, "found": df, "still_zero": de,
                        "blocked": db, "errors": derr, "seconds": int(dt),
                    }
                    rate_5min = dc / dt
                    result["rate_5min"] = round(rate_5min, 3)
                    remaining = total - result["completed"]
                    if rate_5min > 0 and remaining > 0:
                        result["eta_5min_seconds"] = int(remaining / rate_5min)
    except Exception:
        pass
    try:
        if patch_file.exists():
            latest = []
            seen = set()
            with open(patch_file, "r", encoding="utf-8") as f:
                for line in f:
                    e = json.loads(line.strip())
                    word_key = e.get("suchwort", "").casefold()
                    if word_key in seen:
                        continue
                    seen.add(word_key)
                    cnt = int(e.get("count", 0))
                    if cnt > 0:
                        result["fixed"] += 1
                        latest.append({"word": e["suchwort"], "count": cnt})
            result["latest_fixed"] = sorted(latest, key=lambda x: x["count"], reverse=True)[:20]
            result["still_zero"] = max(0, result["completed"] - result["fixed"])
    except Exception:
        pass
    return result


def _read_complete_status() -> dict:
    result = {"total": 0, "completed": 0, "success": 0, "rate": 0, "eta_seconds": 0}
    try:
        if COMPLETE_PROGRESS_FILE.exists():
            with open(COMPLETE_PROGRESS_FILE, "r", encoding="utf-8") as f:
                prog = json.load(f)
            stats = prog.get("stats", {})
            result["total"] = len(prog.get("jobs", [])) + len(prog.get("completed", []))
            result["completed"] = len(prog.get("completed", []))
            result["success"] = stats.get("success", 0)
            started = float(stats.get("started_at", 0))
            if started > 0 and result["completed"] > 0:
                elapsed = time.time() - started
                if elapsed > 0:
                    result["rate"] = round(stats.get("requests", 0) / elapsed, 2)
                    remaining = result["total"] - result["completed"]
                    if result["rate"] > 0 and remaining > 0:
                        result["eta_seconds"] = int(remaining / result["rate"])
    except Exception:
        pass
    return result


RESCRAPE_STDOUT_LOG = OUTPUT_DIR / "sn_rescrape_stdout.log"
CF_SOLVER_LOG = OUTPUT_DIR / "cf_solver.log"

# In-Memory Aktivitaets-Log (Dashboard-Aktionen)
_activity_log: list[dict] = []
ACTIVITY_LOG_MAX = 200


def _activity_add(msg: str, level: str = "info"):
    """Fuegt einen Eintrag zum Aktivitaets-Log hinzu."""
    ts = datetime.now().strftime("%H:%M:%S")
    _activity_log.append({"ts": ts, "msg": msg, "level": level})
    if len(_activity_log) > ACTIVITY_LOG_MAX:
        del _activity_log[: len(_activity_log) - ACTIVITY_LOG_MAX]


def _read_log() -> list[str]:
    log_file = RESCRAPE_STDOUT_LOG if RESCRAPE_STDOUT_LOG.exists() else COMPLETE_LOG_FILE
    if not log_file.exists():
        return []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [l.strip() for l in lines[-50:] if l.strip()]
    except Exception:
        return []


def _read_activity_log() -> list[dict]:
    """Kombiniert Dashboard-Aktivitaets-Log mit Scraper- und CF-Log."""
    entries = list(_activity_log)
    # Scraper-Log (letzte 30 Zeilen)
    if RESCRAPE_STDOUT_LOG.exists():
        try:
            with open(RESCRAPE_STDOUT_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines[-30:]:
                line = line.strip()
                if not line:
                    continue
                entries.append({"ts": "", "msg": line, "level": "scraper"})
        except Exception:
            pass
    # CF-Solver-Log (letzte 10 Zeilen)
    if CF_SOLVER_LOG.exists():
        try:
            with open(CF_SOLVER_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines[-10:]:
                line = line.strip()
                if not line:
                    continue
                entries.append({"ts": "", "msg": line, "level": "cf"})
        except Exception:
            pass
    return entries[-50:]


@app.route("/api/activity-log")
def api_activity_log():
    return jsonify({"entries": _read_activity_log()})


@app.route("/api/activity-log/clear", methods=["POST"])
def api_activity_log_clear():
    _activity_log.clear()
    return jsonify({"ok": True})


# ── Generator: Background-Thread + Status ─────────────────────────────────────

_GEN_RESULT = {}
_GEN_LOCK = threading.Lock()

def _gen_worker(mode, candidates, min_score, model, anzahl=1, thema=None, drehscheibe=None):
    """Laeuft im Background-Thread."""
    global _GEN_RESULT
    try:
        from spruch_app import generator as rhyme_generator
        if anzahl and int(anzahl) > 1:
            sprueche = rhyme_generator.generate_batch(
                anzahl=anzahl, mode=mode, candidates=candidates,
                min_score=min_score, model=model, thema=thema,
                drehscheibe=drehscheibe,
            )
            with _GEN_LOCK:
                _GEN_RESULT = {
                    "ok": len(sprueche) > 0,
                    "sprueche": sprueche,
                    "count": len(sprueche),
                }
            if sprueche:
                _activity_add(
                    f"{len(sprueche)} Sprueche generiert ({mode})",
                    "ok",
                )
            else:
                _activity_add("Batch-Generierung fehlgeschlagen", "err")
        else:
            result = rhyme_generator.generate_spruch_v2(
                mode=mode, candidates=candidates, min_score=min_score,
                model=model, thema=thema, drehscheibe=drehscheibe,
            )
            with _GEN_LOCK:
                _GEN_RESULT = result
            if result.get("ok"):
                _activity_add(
                    f"Spruch generiert ({mode}, Score {result.get('score',0)}/5)",
                    "ok",
                )
            else:
                _activity_add(
                    f"Spruch-Generierung fehlgeschlagen: {result.get('error', '?')}",
                    "err",
                )
    except Exception as e:
        import traceback
        traceback.print_exc()
        with _GEN_LOCK:
            _GEN_RESULT = {"ok": False, "error": str(e)}


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Startet Bauernspruch-Generierung im Background-Thread."""
    try:
        from spruch_app import generator as rhyme_generator
        status = rhyme_generator.get_gen_status()
        if status["running"]:
            return jsonify({"ok": False, "error": "Generierung laeuft bereits"})
        payload = request.json or {}
        mode = payload.get("mode", "long")
        if mode not in ("long", "short"):
            mode = "long"
        candidates = int(payload.get("candidates", 3))
        min_score = int(payload.get("min_score", 3))
        model = payload.get("model") or None
        anzahl = max(1, min(int(payload.get("anzahl", 1)), 20))
        thema = payload.get("thema") or None
        drehscheibe = payload.get("drehscheibe") or None
        _activity_add(f"Spruch generieren (Modus={mode}, anzahl={anzahl}, min_score={min_score}, Modell={model or 'default'})")
        global _GEN_RESULT
        _GEN_RESULT = {}
        t = threading.Thread(target=_gen_worker, args=(mode, candidates, min_score, model, anzahl, thema, drehscheibe), daemon=True)
        t.start()
        return jsonify({"ok": True, "status": "started"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/generate/status")
def api_generate_status():
    """Liefert Live-Status-Log + Ergebnis der Generierung."""
    try:
        from spruch_app import generator as rhyme_generator
        status = rhyme_generator.get_gen_status()
        with _GEN_LOCK:
            result = dict(_GEN_RESULT) if _GEN_RESULT else None
        return jsonify({"status": status, "result": result})
    except Exception as e:
        return jsonify({"status": {}, "result": None, "error": str(e)})


@app.route("/api/generate/cancel", methods=["POST"])
def api_generate_cancel():
    """Bricht die laufende Generierung ab."""
    try:
        from spruch_app import generator as rhyme_generator
        rhyme_generator._status_cancel()
        _activity_add("Spruch-Generierung abgebrochen", "warn")
        return jsonify({"ok": True, "msg": "Abbruch signalisiert"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/generate/history")
def api_generate_history():
    """Liefert die letzten Sprueche."""
    try:
        from spruch_app import generator as rhyme_generator
        return jsonify({
            "history": rhyme_generator.get_history(limit=20),
            "stats": rhyme_generator.get_stats(),
        })
    except Exception as e:
        return jsonify({"history": [], "stats": {}, "error": str(e)})


@app.route("/api/generate/clear", methods=["POST"])
def api_generate_clear():
    """Loescht die Generator-History."""
    try:
        from spruch_app import generator as rhyme_generator
        rhyme_generator.clear_history()
        _activity_add("Generator-History geloescht", "warn")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/notion/sync", methods=["POST"])
def api_notion_sync():
    """Schiebt alle veroeffentlicht=1-Sprueche ohne notion_page_id nach Notion."""
    try:
        from spruch_app import notion_sync
        synced, errors = notion_sync.sync_pending()
        _activity_add(
            "Notion-Sync: " + str(synced) + " Spruch/Sprueche gepusht"
            + (" (" + str(len(errors)) + " Fehler)" if errors else ""),
            "ok" if not errors else "warn",
        )
        return jsonify({"ok": True, "synced": synced, "errors": errors})
    except ValueError as e:
        # config.json fehlt notion_token/db_id
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scrape-status")
def api_scrape_status():
    return jsonify({
        "rescrape": _read_rescrape_status(),
        "complete": _read_complete_status(),
        "log": _read_log(),
        "control": _read_control_status(),
    })


def _detect_mode() -> str:
    def _is_active(ctrl_path):
        if not ctrl_path.exists():
            return False
        try:
            d = json.load(open(ctrl_path, encoding="utf-8"))
            return d.get("status", "") not in ("done", "idle")
        except Exception:
            return False

    sitemap_active = _is_active(OUTPUT_DIR / "sn_sitemap_control.json") or (OUTPUT_DIR / "missing_sitemap_words.json").exists()
    countzero_active = _is_active(OUTPUT_DIR / "sn_countzero_control.json") or (OUTPUT_DIR / "count_zero_words.json").exists()
    snowball_active = _is_active(SNOWBALL_CONTROL_FILE)
    bauernspruch_active = _is_active(OUTPUT_DIR / "sn_bauernspruch_control.json")
    derewo_active = _is_active(OUTPUT_DIR / "sn_derewo_control.json")
    rescrape_v2_active = _is_active(OUTPUT_DIR / "sn_rescrape_control.json")

    if rescrape_v2_active:
        return "rescrape_v2"
    if countzero_active:
        return "countzero"
    if snowball_active:
        return "snowball"
    if bauernspruch_active:
        return "bauernspruch"
    if derewo_active:
        return "derewo"
    if sitemap_active:
        return "sitemap"
    return "rescrape"


def _mode_to_ctrl_file(mode: str) -> Path:
    return {
        "rescrape_v2": OUTPUT_DIR / "sn_rescrape_control.json",
        "countzero": OUTPUT_DIR / "sn_countzero_control.json",
        "snowball": SNOWBALL_CONTROL_FILE,
        "bauernspruch": BAUERNSPRUCH_CONTROL_FILE,
        "derewo": DEREWO_CONTROL_FILE,
        "sitemap": SITEMAP_CONTROL_FILE,
    }.get(mode, RESCRAPE_CONTROL_FILE)


def _mode_to_script(mode: str) -> str:
    return {
        "rescrape_v2": str(Path(__file__).parent / "sn_rescrape_v2.py"),
        "countzero": str(Path(__file__).parent / "sn_countzero_scrape.py"),
        "snowball": str(Path(__file__).parent / "sn_snowball_scrape.py"),
        "bauernspruch": str(Path(__file__).parent / "sn_bauernspruch_scrape.py"),
        "derewo": str(Path(__file__).parent / "sn_derewo_scrape.py"),
        "sitemap": str(Path(__file__).parent / "sn_sitemap_scrape.py"),
    }.get(mode, str(Path(__file__).parent / "sn_rescrape_zeros.py"))


def _read_control_status() -> dict:
    mode = _detect_mode()
    ctrl_file = _mode_to_ctrl_file(mode)
    if not ctrl_file.exists():
        return {"status": "idle", "msg": "", "pid": 0, "speed": "normal"}
    try:
        with open(ctrl_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("speed", "normal")
        pid = int(data.get("pid", 0) or 0)
        if data.get("status") in ("running", "paused"):
            if pid <= 0:
                data["status"] = "stopped"
                data["msg"] = "Ungueltiger Prozessstatus"
                data["pid"] = 0
            else:
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x100000, False, pid)
                    if handle == 0:
                        data["status"] = "stopped"
                        data["msg"] = "Prozess existiert nicht mehr"
                        data["pid"] = 0
                    else:
                        kernel32.CloseHandle(handle)
                except Exception:
                    pass
        ts = float(data.get("ts", 0))
        if data.get("status") == "running" and ts > 0 and (time.time() - ts) > 300:
            data["status"] = "stopped"
            data["msg"] = "Kein Heartbeat seit >5min (wahrscheinlich gecrasht)"
            data["pid"] = 0
        return data
    except Exception:
        return {"status": "idle", "msg": "", "pid": 0, "speed": "normal"}


def _write_control_cmd(status: str, msg: str = "", *, pid: int | None = None, speed: str | None = None):
    mode = _detect_mode()
    ctrl_file = _mode_to_ctrl_file(mode)
    existing = {}
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    data = {
        "status": status,
        "msg": msg,
        "pid": existing.get("pid", 0) if pid is None else pid,
        "ts": time.time(),
        "speed": speed or existing.get("speed", "normal"),
        "mode": existing.get("mode", "browser"),
    }
    tmp = ctrl_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(ctrl_file)


@app.route("/api/scrape/rescrape/speed", methods=["POST"])
def api_rescrape_speed():
    payload = request.json or {}
    speed = payload.get("speed", "normal")
    if speed not in ("slow", "normal", "fast", "ultra", "vpn4"):
        return jsonify({"ok": False, "error": "Ungueltige Geschwindigkeit"})
    ctrl = _read_control_status()
    mode = _detect_mode()
    ctrl_file = _mode_to_ctrl_file(mode)
    existing = {}
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    data = {
        "status": ctrl.get("status", "idle"),
        "msg": f"Geschwindigkeit gesetzt: {speed}",
        "pid": ctrl.get("pid", 0),
        "ts": time.time(),
        "speed": speed,
        "mode": existing.get("mode", "browser"),
    }
    tmp = ctrl_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(ctrl_file)
    return jsonify({"ok": True, "speed": speed})


def _force_kill_scraper():
    global _rescrape_proc
    ctrl_file = COUNTZERO_CONTROL_FILE
    pid = 0
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            pid = int(d.get("pid", 0) or 0)
        except Exception:
            pass
    if _rescrape_proc is not None:
        try:
            _rescrape_proc.kill()
            _rescrape_proc.wait(timeout=3)
        except Exception:
            pass
        _rescrape_proc = None
    elif pid > 0:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
                time.sleep(0.3)
        except Exception:
            pass
    stop_data = {
        "status": "stopped",
        "msg": "Force-stopped",
        "pid": 0,
        "ts": time.time(),
        "speed": "normal",
    }
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                old = json.load(f)
            stop_data["speed"] = old.get("speed", "normal")
            stop_data["mode"] = old.get("mode", "browser")
        except Exception:
            stop_data["mode"] = "browser"
    else:
        stop_data["mode"] = "browser"
    tmp = ctrl_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stop_data, f, ensure_ascii=False)
    tmp.replace(ctrl_file)


@app.route("/api/scrape/rescrape/start", methods=["POST"])
def api_rescrape_start():
    global _rescrape_proc
    ctrl = _read_control_status()
    payload = request.json or {}
    force = payload.get("force", False)

    if ctrl.get("status") == "running" and not force:
        return jsonify({"ok": False, "error": f"Laeuft bereits (PID: {ctrl.get('pid')})"})

    if ctrl.get("status") in ("paused", "stopped") or force:
        _force_kill_scraper()

    mode_override = payload.get("mode")
    _activity_add(f"Scraper gestartet (Modus: {mode_override or 'auto'})")

    ctrl_file = COUNTZERO_CONTROL_FILE
    current_mode = "browser"
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            current_mode = data.get("mode", "browser")
        except Exception:
            pass

    mode = mode_override if mode_override in ("browser", "vpn", "direct") else current_mode

    speed_override = payload.get("speed")

    data = {
        "status": "running",
        "msg": "Gestartet via Dashboard",
        "pid": 0,
        "ts": time.time(),
        "speed": speed_override if speed_override in ("slow", "normal", "fast", "ultra", "vpn4") else "normal",
        "mode": mode,
    }
    tmp = ctrl_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(ctrl_file)

    if mode in ("vpn", "direct"):
        script = str(Path(__file__).parent / "sn_countzero_vpn.py")
    else:
        script = str(Path(__file__).parent / "sn_countzero_scrape.py")

    log_path = str(OUTPUT_DIR / "sn_rescrape_stdout.log")
    log_f = open(log_path, "w", encoding="utf-8", buffering=1)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    _rescrape_proc = subprocess.Popen(
        [sys.executable, "-u", script],
        cwd=str(Path(__file__).parent),
        stdout=log_f,
        stderr=log_f,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    data["pid"] = _rescrape_proc.pid
    data["msg"] = f"Gestartet via Dashboard ({mode}-Modus)"
    tmp = ctrl_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(ctrl_file)

    return jsonify({"ok": True, "pid": _rescrape_proc.pid, "mode": mode})


@app.route("/api/scrape/rescrape/pause", methods=["POST"])
def api_rescrape_pause():
    _local_only()
    ctrl = _read_control_status()
    if ctrl.get("status") != "running":
        return jsonify({"ok": False, "error": f"Nicht am Laufen (Status: {ctrl.get('status')})"})
    _write_control_cmd("pause", "Pausiert via Dashboard")
    return jsonify({"ok": True})


@app.route("/api/scrape/rescrape/resume", methods=["POST"])
def api_rescrape_resume():
    ctrl = _read_control_status()
    if ctrl.get("status") != "paused":
        return jsonify({"ok": False, "error": f"Nicht pausiert (Status: {ctrl.get('status')})"})
    _write_control_cmd("running", "Fortgesetzt via Dashboard")
    return jsonify({"ok": True})


@app.route("/api/scrape/rescrape/stop", methods=["POST"])
def api_rescrape_stop():
    ctrl = _read_control_status()
    if ctrl.get("status") not in ("running", "paused"):
        return jsonify({"ok": False, "error": f"Nicht aktiv (Status: {ctrl.get('status')})"})
    _write_control_cmd("stop", "Gestoppt via Dashboard")
    return jsonify({"ok": True})


@app.route("/api/scrape/rescrape/test", methods=["POST"])
def api_rescrape_test():
    n = request.json.get("n", 10) if request.json else 10
    n = max(1, min(n, 50))
    use_sitemap = (OUTPUT_DIR / "missing_sitemap_words.json").exists()
    use_derewo = not use_sitemap and (OUTPUT_DIR / "missing_derewo_scrape.json").exists()
    if use_sitemap:
        script = str(Path(__file__).parent / "sn_sitemap_scrape.py")
    elif use_derewo:
        script = str(Path(__file__).parent / "sn_derewo_scrape.py")
    else:
        script = str(Path(__file__).parent / "sn_rescrape_zeros.py")
    proc = subprocess.run(
        [sys.executable, script, "--test", str(n)],
        cwd=str(Path(__file__).parent),
        capture_output=True, text=True, timeout=120,
    )
    try:
        results = json.loads(proc.stdout)
    except Exception:
        results = [{"word": "Fehler", "status": "error", "detail": proc.stderr[:200] or "Unbekannter Fehler"}]
    return jsonify({"ok": True, "results": results, "n": n})


@app.route("/api/scrape/countzero/mode", methods=["GET"])
def api_countzero_get_mode():
    """Aktuellen Modus (browser/vpn) abrufen"""
    ctrl_file = COUNTZERO_CONTROL_FILE
    mode = "browser"
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            mode = data.get("mode", "browser")
        except Exception:
            pass
    return jsonify({"ok": True, "mode": mode})


@app.route("/api/scrape/countzero/mode", methods=["POST"])
def api_countzero_set_mode():
    """Modus (browser/vpn) setzen"""
    payload = request.json or {}
    mode = payload.get("mode", "browser")
    if mode not in ("browser", "vpn", "direct"):
        return jsonify({"ok": False, "error": "Ungueltiger Modus. Erlaubt: browser, vpn, direct"})

    ctrl_file = COUNTZERO_CONTROL_FILE
    existing = {}
    if ctrl_file.exists():
        try:
            with open(ctrl_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    data = {
        "status": existing.get("status", "idle"),
        "msg": f"Modus gewechselt zu {mode}",
        "pid": existing.get("pid", 0),
        "ts": time.time(),
        "speed": existing.get("speed", "normal"),
        "mode": mode,
    }
    tmp = ctrl_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(ctrl_file)

    return jsonify({"ok": True, "mode": mode})


@app.route("/download/sprachnudel-export")
def download_sprachnudel_export():
    if not SN_EXPORT_V12_FILE.exists():
        return jsonify({"error": "Export-Datei noch nicht erzeugt"}), 404
    return send_file(
        SN_EXPORT_V12_FILE,
        as_attachment=True,
        download_name="sprachnudel_export.v12.json",
        mimetype="application/json",
    )


# ─── VPN-Steuerung (Surfshark via WireGuard) ─────────────────────────

@app.route("/api/vpn/status")
def api_vpn_status():
    """VPN-Status abfragen"""
    try:
        import surfshark_vpn
        status = surfshark_vpn.get_status()
        status["is_admin"] = surfshark_vpn.is_admin()
        return jsonify({"ok": True, **status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/vpn/connect", methods=["POST"])
def api_vpn_connect():
    _local_only()
    """VPN verbinden"""
    try:
        import surfshark_vpn
        config_name = (request.json or {}).get("config")
        _activity_add(f"VPN verbinden: {config_name or 'auto'}")
        result = surfshark_vpn.connect(config_name)
        _activity_add(f"VPN verbunden: {result.get('server','?')} OK={result.get('ok')}", "ok" if result.get("ok") else "err")
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/vpn/disconnect", methods=["POST"])
def api_vpn_disconnect():
    """VPN trennen — mit Force-Option wenn normaler Disconnect scheitert"""
    try:
        import surfshark_vpn
        force = (request.json or {}).get("force", False)
        _activity_add("VPN trennen" + (" (FORCE)" if force else ""))
        result = surfshark_vpn.disconnect()
        if not result.get("ok") and ("denied" in result.get("error", "").lower() or "access" in result.get("error", "").lower()):
            # WireGuard laeuft als Admin — Force-Disconnect via Admin-Task
            force_cmd = (
                'taskkill /IM wg.exe /F 2>nul & '
                'taskkill /IM wireguard.exe /F 2>nul & '
                'powershell -Command "Get-NetAdapter | Where-Object '
                '{ $_.InterfaceDescription -like \'*WireGuard*\' -or $_.InterfaceDescription -like \'*Wintun*\' } '
                '| Disable-NetAdapter -Confirm:0"'
            )
            subprocess.run(
                ["schtasks", "/create", "/tn", "VPN_ForceDisconnect", "/tr",
                 f"cmd /c {force_cmd}",
                 "/sc", "once", "/st", "00:00", "/rl", "HIGHEST", "/it", "/f"],
                capture_output=True, text=True,
            )
            subprocess.run(["schtasks", "/run", "/tn", "VPN_ForceDisconnect"], capture_output=True)
            time.sleep(2)
            subprocess.run(["schtasks", "/delete", "/tn", "VPN_ForceDisconnect", "/f"], capture_output=True)
            _activity_add("VPN FORCE-getrennt (Admin-Prozess gekillt)", "warn")
            return jsonify({"ok": True, "msg": "VPN FORCE-getrennt"})
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/vpn/switch", methods=["POST"])
def api_vpn_switch():
    """VPN-Server wechseln"""
    try:
        import surfshark_vpn
        config_name = (request.json or {}).get("config")
        if not config_name:
            return jsonify({"ok": False, "error": "config Name erforderlich"})
        _activity_add(f"VPN wechseln zu: {config_name}")
        result = surfshark_vpn.switch_server(config_name)
        _activity_add(f"VPN gewechselt: OK={result.get('ok')}", "ok" if result.get("ok") else "err")
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/vpn/configs")
def api_vpn_configs():
    """Verfügbare VPN-Configs auflisten"""
    try:
        import surfshark_vpn
        configs = surfshark_vpn.get_available_configs()
        return jsonify({"ok": True, "configs": configs, "count": len(configs)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/vpn/ip")
def api_vpn_ip():
    """Aktuelle öffentliche IP abfragen"""
    try:
        import surfshark_vpn
        ip = surfshark_vpn.get_current_ip()
        return jsonify({"ok": True, "ip": ip})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/vpn/rotate", methods=["POST"])
def api_vpn_rotate():
    """VPN-Server automatisch rotieren (zufälliger anderer Server)"""
    try:
        import surfshark_vpn
        result = surfshark_vpn.rotate_server()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/restart-admin", methods=["POST"])
def api_restart_admin():
    _local_only()
    """Flask als Admin neu starten — beendet ALLE python.exe, startet dann neu als Admin"""
    try:
        import ctypes
        script = str(Path(__file__).resolve())
        # Admin-Prozess soll erst alle alten Python-Prozesse killen, dann Flask starten
        cmd = (
            f"Get-Process python -ErrorAction SilentlyContinue | Where-Object {{$_.Id -ne $PID}} | "
            f"Stop-Process -Force -ErrorAction SilentlyContinue; "
            f"Start-Sleep -Seconds 2; "
            f"cd '{Path(__file__).parent}'; python '{script}'"
        )
        params = f'-NoExit -Command "{cmd}"'
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, 1)
        # Aktueller Prozess sofort beenden (macht Platz für den Admin-Prozess)
        import threading
        def _delayed_exit():
            import time
            time.sleep(3)
            import os
            os._exit(0)
        threading.Thread(target=_delayed_exit, daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


_cf_solver_proc = None
_cf_solver_log_path = None


class _CfSolverMonitor:
    """Ueberwacht den CF-Solver via Log-Datei (fuer Task-Scheduler-Prozesse)."""

    def __init__(self, log_path, timeout=180):
        self.log_path = str(log_path)
        self.start_time = time.time()
        self.timeout = timeout

    def poll(self):
        """Returns None if solver likely still running, 0 if done, 1 if error/timeout."""
        if time.time() - self.start_time > self.timeout:
            return 1  # timeout
        try:
            with open(self.log_path, encoding="utf-8") as f:
                content = f.read()
            if "Fertig, Erfolg=True" in content:
                return 0
            if "FEHLER" in content or "Traceback" in content:
                return 1
        except Exception:
            pass
        return None  # still running


@app.route("/api/cf/cookies/status")
def api_cf_cookies_status():
    """Prüft ob CF-Cookies vorhanden sind und ob Solver läuft"""
    try:
        from cf_solver import get_cf_cookies
        cookies = get_cf_cookies()
        has_cookies = len(cookies) > 0
        solver_running = _cf_solver_proc is not None and _cf_solver_proc.poll() is None
        solver_log = ""
        if _cf_solver_log_path and Path(_cf_solver_log_path).exists():
            try:
                with open(_cf_solver_log_path) as f:
                    lines = f.readlines()
                    solver_log = "".join(lines[-15:])
            except Exception:
                pass
        result = {
            "ok": True,
            "has_cookies": has_cookies,
            "count": len(cookies),
            "solver_running": solver_running,
        }
        if solver_log:
            result["solver_log"] = solver_log
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/cf/cookies/fetch", methods=["POST"])
def api_cf_cookies_fetch():
    _local_only()
    """Holt neue CF-Cookies via cf_solver.py --direct-solve als NON-ELEVATED Prozess"""
    global _cf_solver_proc, _cf_solver_log_path
    import subprocess
    import sys
    import time
    from pathlib import Path

    # Prüfen ob schon einer läuft
    if _cf_solver_proc and _cf_solver_proc.poll() is None:
        return jsonify({"ok": False, "error": f"Solver laeuft bereits (PID {_cf_solver_proc.pid})"})

    _activity_add("CF-Cookies holen gestartet")

    base = Path(__file__).parent
    cf_path = base / "cf_solver.py"
    log_path = base / "output" / "cf_solver.log"
    _cf_solver_log_path = str(log_path)

    # Log-File leeren
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "w") as f:
        f.write("")

    # Methode: Task-Scheduler startet Prozess als NON-ELEVATED (Limited)
    # Wichtig weil Chrome als Admin nicht funktioniert (nodriver "Failed to connect")
    task_name = "CF_Solver_Temp"

    # Alte Task loeschen falls vorhanden
    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"],
                   capture_output=True)

    # Task erstellen: /rl LIMITED = non-elevated, /it = interaktiv (Browser sichtbar)
    cmd = f'"{sys.executable}" "{cf_path}" --direct-solve'
    create_result = subprocess.run(
        ["schtasks", "/create", "/tn", task_name, "/tr", cmd,
         "/sc", "once", "/st", "00:00", "/rl", "LIMITED", "/it", "/f"],
        capture_output=True, text=True,
    )

    if create_result.returncode != 0:
        # Fallback: normaler subprocess (falls schtasks nicht klappt)
        print(f"CF-Solver: schtasks fehlgeschlagen ({create_result.stderr}), nutze Fallback", flush=True)
        proc = subprocess.Popen(
            [sys.executable, str(cf_path), "--direct-solve"],
            cwd=str(base),
        )
        _cf_solver_proc = proc
        return jsonify({
            "ok": True,
            "message": f"CF-Solver gestartet (Fallback PID {proc.pid})",
            "pid": proc.pid,
        })

    # Task sofort ausfuehren
    subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True)
    print(f"CF-Solver: Via Task-Scheduler gestartet (non-elevated)", flush=True)

    # Task aufraeumen nach kurzem Delay
    time.sleep(2)
    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True)

    # Proxy-Process-Objekt fuer Status-Tracking (poll liest Log)
    _cf_solver_proc = _CfSolverMonitor(log_path)

    return jsonify({
        "ok": True,
        "message": "CF-Solver gestartet (non-elevated). Browser oeffnet sich gleich!",
    })


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("DEBUG", "0").strip() in {"1", "true", "yes", "on"}
    # v12 Index im Hintergrund laden (non-blocking)
    _preload = threading.Thread(target=_load_v12, daemon=True)
    _preload.start()
    app.run(debug=debug, use_reloader=False, host=host, port=port, threaded=True)
