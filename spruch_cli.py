"""
spruch_cli.py – CLI für Bauernspruch-Generator

Nutzung:
  python spruch_cli.py --mode short --anzahl 5 --debug
  python spruch_cli.py --mode long --anzahl 3 --seed 42 --output sprueche.txt
  python spruch_cli.py --help
"""

import argparse
import json
import os
import random
from pathlib import Path

from spruch_app.generator import (
    generate_spruch, load_gruppen, pick_gruppe,
    session_stats, cost_report,
)

APP_DIR      = Path(__file__).resolve().parent
DEFAULT_DATA = APP_DIR / "output" / "reimgruppen_derb.jsonl"


def _load_api_key():
    key = os.environ.get("GLM_API_KEY", "")
    if key:
        return key
    cfg = APP_DIR / "config.json"
    if cfg.exists():
        try:
            return json.load(open(cfg, encoding="utf-8")).get("api_key", "")
        except Exception:
            pass
    return ""


def ask_mode(current=None):
    if current in {"short", "long"}:
        return current
    while True:
        val = input("Modus (short/long): ").strip().lower()
        if val in {"short", "long"}:
            return val
        print("Bitte 'short' oder 'long' eingeben.")


def ask_anzahl(current=None):
    if isinstance(current, int) and current > 0:
        return current
    while True:
        val = input("Anzahl Sprueche: ").strip()
        try:
            n = int(val)
        except ValueError:
            n = 0
        if n > 0:
            return n
        print("Bitte eine positive Zahl eingeben.")


def export_to_txt(sprueche, output_path):
    ziel = Path(output_path)
    with open(ziel, "w", encoding="utf-8") as f:
        for i, spruch in enumerate(sprueche, start=1):
            f.write(str(i) + ".\n" + spruch + "\n\n")
    return ziel


def build_parser():
    p = argparse.ArgumentParser(description="CLI für Bauernspruch-Generator")
    p.add_argument("--mode",   choices=["short", "long"], help="Spruchmodus")
    p.add_argument("--anzahl", type=int,                  help="Anzahl der Sprueche")
    p.add_argument("--data",   default=str(DEFAULT_DATA), help="Pfad zur JSONL-Datei")
    p.add_argument("--seed",   type=int,                  help="Zufalls-Seed fuer Reproduzierbarkeit")
    p.add_argument("--output",                            help="TXT-Exportpfad")
    p.add_argument("--model",                             help="GLM-Modell ueberschreiben")
    p.add_argument("--debug",  action="store_true",       help="Debug-Logging aktivieren")
    p.add_argument("--max-versuche", type=int, default=20,
                   help="Max. Generierungsversuche pro Spruch (default: 20)")
    return p


def main():
    parser = build_parser()
    args   = parser.parse_args()

    api_key = _load_api_key()
    if not api_key:
        raise ValueError(
            "GLM_API_KEY nicht gesetzt und kein api_key in config.json gefunden."
        )

    mode   = ask_mode(args.mode)
    anzahl = ask_anzahl(args.anzahl)

    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError("Datei nicht gefunden: " + str(data_path))

    gruppen = load_gruppen(data_path)
    if not gruppen:
        raise ValueError("Keine Reimgruppen gefunden.")

    print("\nGeladen: " + str(len(gruppen)) + " Gruppen | Modus: " + mode + " | Ziel: " + str(anzahl) + " Sprueche")
    print("-" * 50)

    rnd         = random.Random(args.seed)
    sprueche    = []
    versuche    = 0
    max_v       = args.max_versuche

    while len(sprueche) < anzahl and versuche < max_v:
        versuche += 1
        gruppe   = pick_gruppe(gruppen, rnd)
        result   = generate_spruch(
            gruppe, api_key,
            mode=mode, rnd=rnd,
            debug=args.debug,
            model=args.model,
        )

        if result["ok"]:
            sprueche.append(result["spruch"])
            k = result.get("kosten_usd", 0)
            print("[" + str(len(sprueche)) + "/" + str(anzahl) + "] OK  $" + format(k, ".6f"))
        else:
            print("[" + str(len(sprueche)) + "/" + str(anzahl) + "] X   Reim fehlgeschlagen")

    print("-" * 50)

    for i, spruch in enumerate(sprueche, start=1):
        print("\n" + str(i) + ".")
        print(spruch)

    s = session_stats()
    t = s["tokens"]
    print("\n" + "-" * 50)
    print("Sprueche generiert : " + str(len(sprueche)) + " / " + str(anzahl))
    print("API-Calls gesamt   : " + str(s["calls"]))
    print("Tokens             : " + str(t["prompt"]) + " prompt + " + str(t["completion"]) + " completion = " + str(t["gesamt"]) + " gesamt")
    print("Kosten Session     : $" + format(s["kosten_usd"], ".6f") + " USD  (~" + format(s["kosten_usd"]*100, ".4f") + " Cent)")

    r = cost_report()
    print()
    print("-- Kumulierte Kosten (aus cost_log.json) ---------")
    for label, key in [("Heute", "heute"), ("Woche", "woche"),
                        ("Monat", "monat"), ("Gesamt", "gesamt")]:
        b = r[key]
        print(label.ljust(8) + ": $" + format(b["kosten_usd"], ".6f") +
              "  (" + str(b["calls"]) + " Calls, " + str(b["tokens"]) + " Tokens)")

    if args.output and sprueche:
        ziel = export_to_txt(sprueche, args.output)
        print("\nGespeichert: " + str(ziel))


if __name__ == "__main__":
    main()
