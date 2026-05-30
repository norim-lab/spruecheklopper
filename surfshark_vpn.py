"""
Surfshark VPN Steuerung via WireGuard CLI.
Ermöglicht Connect/Disconnect/Status-Abfrage und Server-Wechsel
direkt aus dem Dashboard heraus.

Voraussetzungen:
  1. WireGuard installiert (C:\\Program Files\\WireGuard\\)
  2. Surfshark WireGuard-Configs in vpn_configs/ Ordner
     (von https://account.surfshark.com/setup/manual generieren)
"""

import subprocess
import os
import re
import json
import time
from pathlib import Path
from typing import Optional

# Pfade
WIREGUARD_DIR = Path(r"C:\Program Files\WireGuard")
WG_EXE = WIREGUARD_DIR / "wg.exe"
WIREGUARD_EXE = WIREGUARD_DIR / "wireguard.exe"
CONFIG_DIR = Path(__file__).parent / "vpn_configs"

# Der WireGuard Tunnel-Name (wird als Windows-Service-Name verwendet)
TUNNEL_NAME = "surfshark"


def _detect_active_tunnel_name() -> str:
    """Findet den Namen des aktiven WireGuard-Tunnells (z.B. 'Budapest' statt 'surfshark')."""
    try:
        r = _run([str(WG_EXE), "show"])
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("interface:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        r = _run(["powershell", "-Command",
                   "Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'WireGuard' -and $_.Status -eq 'Up' } | "
                   "Select-Object -ExpandProperty Name | ConvertTo-Json"])
        if r.returncode == 0 and r.stdout.strip():
            name = r.stdout.strip().strip('"')
            if name:
                return name
    except Exception:
        pass
    return TUNNEL_NAME


def _run_admin(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    """Führt wireguard.exe aus. Erfordert dass der Prozess als Admin läuft."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="Timeout")
    except Exception as e:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=str(e))


def is_admin() -> bool:
    """Prüft ob der aktuelle Prozess Admin-Rechte hat."""
    import ctypes
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Führt einen Befehl normal aus."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def is_wireguard_installed() -> bool:
    """Prüft ob WireGuard installiert ist."""
    return WG_EXE.exists() and WIREGUARD_EXE.exists()


def get_available_configs() -> list[dict]:
    """Listet alle verfügbaren Surfshark WireGuard-Config-Dateien auf."""
    configs = []
    if not CONFIG_DIR.exists():
        return configs
    for f in sorted(CONFIG_DIR.glob("*.conf")):
        # Dateiname wird als Server-Name verwendet
        name = f.stem
        # Versuche Location aus Config zu lesen
        endpoint = ""
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("Endpoint"):
                        endpoint = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
        configs.append({
            "name": name,
            "file": str(f),
            "endpoint": endpoint,
        })
    return configs


def get_status() -> dict:
    """
    Liefert den aktuellen VPN-Status.
    Prüft den WireGuard-Tunnel-Status und den Netzwerkadapter.
    """
    result = {
        "installed": is_wireguard_installed(),
        "connected": False,
        "tunnel_name": TUNNEL_NAME,
        "interface": {},
        "peer": {},
        "adapter": None,
        "config_count": len(get_available_configs()),
        "error": None,
    }

    if not is_wireguard_installed():
        result["error"] = "WireGuard nicht installiert"
        return result

    # wg show – versuche zuerst aktiven Tunnel zu finden
    active_tunnel = _detect_active_tunnel_name()
    try:
        r = _run([str(WG_EXE), "show", active_tunnel])
        if r.returncode == 0 and r.stdout.strip():
            result["connected"] = True
            result["tunnel_name"] = active_tunnel
            # Parse wg show Output
            current_section = None
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Sections: interface, peer
                if line.startswith("interface:"):
                    current_section = "interface"
                    result["interface"]["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("peer:"):
                    current_section = "peer"
                    result["peer"]["public_key"] = line.split(":", 1)[1].strip()
                elif current_section == "interface":
                    if ":" in line:
                        key, val = line.split(":", 1)
                        result["interface"][key.strip()] = val.strip()
                elif current_section == "peer":
                    if ":" in line:
                        key, val = line.split(":", 1)
                        result["peer"][key.strip()] = val.strip()
    except Exception as e:
        result["error"] = f"wg show Fehler: {e}"

    # Netzwerkadapter prüfen
    try:
        r = _run(["powershell", "-Command",
                   "Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'WireGuard' } | "
                   "Select-Object Name,Status,InterfaceDescription | ConvertTo-Json"])
        if r.returncode == 0 and r.stdout.strip():
            adapter = json.loads(r.stdout)
            if isinstance(adapter, list):
                adapter = adapter[0] if adapter else None
            if adapter:
                result["adapter"] = adapter
                if adapter.get("Status") == "Up":
                    result["connected"] = True
    except Exception:
        pass

    return result


def connect(config_name: Optional[str] = None) -> dict:
    """
    Verbindet mit einem Surfshark WireGuard-Tunnel.
    
    Args:
        config_name: Name der Config (ohne .conf). Falls None, wird die
                     erste verfügbare Config verwendet.
    
    Returns:
        dict mit ok=True/False und Details.
    """
    if not is_wireguard_installed():
        return {"ok": False, "error": "WireGuard nicht installiert"}

    # Wenn bereits verbunden, zuerst trennen
    status = get_status()
    if status["connected"]:
        disconnect()

    # Config finden
    configs = get_available_configs()
    if not configs:
        return {
            "ok": False,
            "error": "Keine WireGuard-Configs gefunden. Bitte Configs in vpn_configs/ ablegen.",
            "hint": "Gehe zu https://account.surfshark.com/setup/manual und generiere WireGuard-Configs.",
        }

    config_path = None
    if config_name:
        for c in configs:
            if c["name"] == config_name:
                config_path = c["file"]
                break
        if not config_path:
            return {"ok": False, "error": f"Config '{config_name}' nicht gefunden"}
    else:
        import random
        config_path = random.choice(configs)["file"]

    # WireGuard Tunnel installieren (erstellt Windows-Service)
    try:
        r = _run_admin([str(WIREGUARD_EXE), "/installtunnelservice", config_path], timeout=20)
        if r.returncode != 0:
            error_msg = r.stderr.strip() or r.stdout.strip() or f"Exit code {r.returncode}"
            # Prüfe ob Tunnel bereits existiert
            if "already exists" in error_msg.lower() or "bereits" in error_msg.lower():
                # Tunnel existiert bereits, versuche nur zu aktivieren
                return _activate_existing_tunnel(config_path)
            return {"ok": False, "error": f"WireGuard Fehler: {error_msg}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "WireGuard Timeout beim Verbinden"}
    except Exception as e:
        return {"ok": False, "error": f"Verbindungsfehler: {e}"}

    # Kurz warten und Status prüfen
    time.sleep(2)
    new_status = get_status()
    if new_status["connected"]:
        return {
            "ok": True,
            "message": f"Verbunden via {Path(config_path).stem}",
            "tunnel": Path(config_path).stem,
        }
    else:
        return {
            "ok": False,
            "error": "Tunnel wurde installiert aber Adapter ist nicht Up",
            "status": new_status,
        }


def _activate_existing_tunnel(config_path: str) -> dict:
    """Versucht einen existierenden Tunnel zu aktivieren."""
    try:
        # wg set um den Tunnel zu aktivieren
        r = _run([str(WG_EXE), "set", TUNNEL_NAME], timeout=10)
        time.sleep(2)
        new_status = get_status()
        if new_status["connected"]:
            return {"ok": True, "message": f"Verbunden (existierender Tunnel)"}
        return {"ok": False, "error": "Konnte existierenden Tunnel nicht aktivieren"}
    except Exception as e:
        return {"ok": False, "error": f"Fehler: {e}"}


def disconnect() -> dict:
    """Trennt die WireGuard VPN-Verbindung."""
    if not is_wireguard_installed():
        return {"ok": False, "error": "WireGuard nicht installiert"}

    active_tunnel = _detect_active_tunnel_name()
    try:
        r = _run_admin([str(WIREGUARD_EXE), "/uninstalltunnelservice", active_tunnel], timeout=15)
        if r.returncode != 0:
            error_msg = r.stderr.strip() or r.stdout.strip()
            # "does not exist" ist OK - Tunnel war nicht aktiv
            if "does not exist" in error_msg.lower() or "nicht" in error_msg.lower():
                return {"ok": True, "message": "Tunnel war nicht aktiv"}
            return {"ok": False, "error": f"Disconnect Fehler: {error_msg}"}

        time.sleep(1)
        return {"ok": True, "message": "VPN getrennt"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Disconnect Timeout"}
    except Exception as e:
        return {"ok": False, "error": f"Disconnect Fehler: {e}"}


def switch_server(config_name: str) -> dict:
    """Wechselt zu einem anderen Server (Disconnect + Connect)."""
    disconnect()
    time.sleep(1)
    return connect(config_name)


# Zustand für Rotation
_last_server: Optional[str] = None
_rotation_history: list[str] = []
_MAX_HISTORY = 20


def rotate_server(exclude_current: bool = True, retry_count: int = 3) -> dict:
    """
    Wechselt automatisch zu einem zufälligen anderen VPN-Server.
    
    Args:
        exclude_current: Aktuellen Server ausschließen
        retry_count: Max. Versuche bei Fehler
    
    Returns:
        dict mit ok=True/False
    """
    global _last_server, _rotation_history
    
    import random
    
    configs = get_available_configs()
    if not configs:
        return {"ok": False, "error": "Keine VPN-Configs verfügbar"}
    
    # Aktuellen Server ermitteln
    current_server = None
    status = get_status()
    if status.get("connected"):
        # Versuche Tunnelnamen aus Interface-Info zu lesen
        iface = status.get("interface", {})
        current_server = iface.get("name", _last_server)
    if not current_server:
        current_server = _last_server
    
    # Kandidaten filtern
    candidates = [c["name"] for c in configs]
    if exclude_current and current_server:
        candidates = [c for c in candidates if c != current_server]
    # Zuletzt verwendete Server ebenfalls ausschließen (Rotation)
    if _rotation_history:
        recent = _rotation_history[-5:]
        candidates = [c for c in candidates if c not in recent]
    if not candidates:
        # Fallback: alle außer aktuellen
        candidates = [c["name"] for c in configs if c != current_server]
    if not candidates:
        candidates = [c["name"] for c in configs]
    
    for attempt in range(retry_count):
        chosen = random.choice(candidates)
        result = switch_server(chosen)
        if result.get("ok"):
            _last_server = chosen
            _rotation_history.append(chosen)
            if len(_rotation_history) > _MAX_HISTORY:
                _rotation_history = _rotation_history[-_MAX_HISTORY:]
            # Neue IP prüfen
            time.sleep(2)
            new_ip = get_current_ip()
            result["new_ip"] = new_ip
            result["server"] = chosen
            return result
        # Bei Fehler: anderen Server versuchen
        candidates = [c for c in candidates if c != chosen]
        if not candidates:
            candidates = [c["name"] for c in configs]
        time.sleep(2)
    
    return {"ok": False, "error": f"Rotation nach {retry_count} Versuchen fehlgeschlagen"}


def get_rotation_history() -> list[str]:
    """Gibt die History der Server-Rotation zurück."""
    return list(_rotation_history)


def get_current_ip() -> Optional[str]:
    """Ermittelt die aktuelle öffentliche IP-Adresse."""
    import urllib.request
    urls = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.64"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                ip = resp.read().decode().strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                    return ip
        except Exception:
            continue
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Verwendung: python surfshark_vpn.py [status|connect|disconnect|list|ip] [config_name]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "status":
        s = get_status()
        print(json.dumps(s, indent=2, ensure_ascii=False))
    elif cmd == "list":
        configs = get_available_configs()
        if not configs:
            print("Keine Configs gefunden. Bitte in vpn_configs/ ablegen.")
        for c in configs:
            print(f"  {c['name']:30s}  {c['endpoint']}")
    elif cmd == "connect":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        result = connect(name)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "disconnect":
        result = disconnect()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "ip":
        ip = get_current_ip()
        print(f"Aktuelle IP: {ip}")
    else:
        print(f"Unbekannter Befehl: {cmd}")
