"""
Surfshark WireGuard Config Generator (KEIN Login nötig!)

Nimmt eine bestehende WireGuard-Config von https://account.surfshark.com/setup/manual
und generiert Configs für ALLE Surfshark-Server automatisch.

Verwendung:
  python generate_vpn_configs.py

Du brauchst nur EINE .conf-Datei von der Surfshark-Website.
Der PrivateKey daraus gilt für alle Server.
"""

import json
import os
import socket
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

CONFIG_DIR = Path(__file__).parent / "vpn_configs"
TEMPLATE_CONFIG = CONFIG_DIR / "_template.conf"


def parse_existing_config() -> str | None:
    """Liest den PrivateKey aus einer bestehenden Config."""
    # 1. Prüfe _template.conf im vpn_configs Ordner
    if TEMPLATE_CONFIG.exists():
        return _extract_private_key(TEMPLATE_CONFIG.read_text(encoding="utf-8"))

    # 2. Prüfe erste .conf im vpn_configs Ordner
    if CONFIG_DIR.exists():
        for f in CONFIG_DIR.glob("*.conf"):
            if f.name == "_template.conf":
                continue
            pk = _extract_private_key(f.read_text(encoding="utf-8"))
            if pk:
                return pk
    return None


def _extract_private_key(content: str) -> str | None:
    """Extrahiert PrivateKey aus einer WireGuard Config."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("PrivateKey"):
            return line.split("=", 1)[1].strip()
    return None


def get_server_list() -> list[dict]:
    """Holt die Serverliste von Surfshark (öffentlich, kein Login nötig)."""
    url = "https://api.surfshark.com/v4/server/clusters/generic"
    response = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    if response.ok:
        servers = response.json()
        unique = {s['location']: s for s in servers}.values()
        return sorted(unique, key=lambda x: x['location'])
    else:
        print(f"  Serverliste fehlgeschlagen: {response.status_code}")
        return []


def build_configs(private_key: str, servers: list[dict]) -> int:
    """Erstellt WireGuard-Config-Dateien für alle Server."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    failed = 0

    for server in servers:
        connection_name = server.get('connectionName', '')
        pub_key = server.get('pubKey', '')
        if not connection_name or not pub_key:
            continue

        # Endpoint direkt als Hostname verwenden (kein DNS nötig)
        location = server['location'].replace(" ", "_")

        config_content = (
            f"[Interface]\n"
            f"PrivateKey = {private_key}\n"
            f"Address = 10.14.0.2/16\n"
            f"DNS = 162.252.172.57, 149.154.159.92\n"
            f"\n"
            f"[Peer]\n"
            f"PublicKey = {pub_key}\n"
            f"AllowedIPs = 0.0.0.0/0\n"
            f"Endpoint = {connection_name}:51820\n"
            f"PersistentKeepalive = 25\n"
        )

        conf_path = CONFIG_DIR / f"{location}.conf"
        conf_path.write_text(config_content, encoding="utf-8")
        count += 1
        if count <= 5 or count % 100 == 0:
            print(f"  {location:40s} -> {connection_name}:51820")

    return count


def main():
    print("=" * 60)
    print("  Surfshark WireGuard Config Generator")
    print("  (Kein Login nötig!)")
    print("=" * 60)

    # Prüfe ob wir schon einen PrivateKey haben
    private_key = parse_existing_config()

    if private_key:
        print(f"\n  PrivateKey gefunden: {private_key[:15]}...{private_key[-5:]}")
    else:
        print("\n  Kein PrivateKey gefunden.")
        print("  Bitte eine WireGuard-Config von Surfshark herunterladen:")
        print("  1. Öffne https://account.surfshark.com/setup/manual")
        print("  2. WireGuard wählen → Config für ein Land herunterladen")
        print(f"  3. Config-Datei in {CONFIG_DIR}\\ Ordner kopieren")
        print("     (oder den Inhalt hier einfügen)")
        print()

        # User kann Config-Inhalt einfügen
        print("  Config-Inhalt einfügen (leere Zeile zum Beenden):")
        lines = []
        while True:
            line = input().strip()
            if not line:
                break
            lines.append(line)

        content = "\n".join(lines)
        private_key = _extract_private_key(content)

        if not private_key:
            print("  FEHLER: Kein PrivateKey in der Config gefunden!")
            sys.exit(1)

        # Als Template speichern
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TEMPLATE_CONFIG.write_text(content, encoding="utf-8")
        print(f"  Template gespeichert: {TEMPLATE_CONFIG}")

    print(f"\n  Lade Serverliste von Surfshark...")
    servers = get_server_list()
    print(f"  {len(servers)} Server gefunden\n")

    if not servers:
        print("  FEHLER: Serverliste konnte nicht geladen werden!")
        sys.exit(1)

    print("  Generiere Configs...")
    count = build_configs(private_key, servers)
    print(f"\n  Fertig! {count} VPN-Configs in {CONFIG_DIR}\\ erstellt.")
    print("  Dashboard -> VPN-Panel kann jetzt Server auswählen und verbinden.")


if __name__ == "__main__":
    main()
