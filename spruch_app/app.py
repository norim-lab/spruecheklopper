import argparse
import json
from pathlib import Path
import random

from generator import generate_spruch


APP_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = APP_DIR.parent / "output" / "reimgruppen.json"


def load_reimgruppen(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("reimgruppen.json muss ein Array sein.")
    return data


def ask_mode(current=None):
    if current in {"short", "long"}:
        return current
    while True:
        value = input("Modus (short/long): ").strip().lower()
        if value in {"short", "long"}:
            return value
        print("Bitte 'short' oder 'long' eingeben.")


def ask_anzahl(current=None):
    if isinstance(current, int) and current > 0:
        return current
    while True:
        value = input("Anzahl Sprueche: ").strip()
        try:
            amount = int(value)
        except ValueError:
            amount = 0
        if amount > 0:
            return amount
        print("Bitte eine positive Zahl eingeben.")


def export_to_txt(sprueche, output_path):
    ziel = Path(output_path)
    with open(ziel, "w", encoding="utf-8") as f:
        for index, spruch in enumerate(sprueche, start=1):
            f.write(f"{index}. {spruch}\n\n")
    return ziel


def build_parser():
    parser = argparse.ArgumentParser(description="CLI fuer Bauernsprueche aus reimgruppen.json")
    parser.add_argument("--mode", choices=["short", "long"], help="Spruchmodus")
    parser.add_argument("--anzahl", type=int, help="Anzahl der Sprueche")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="Pfad zur reimgruppen.json")
    parser.add_argument("--seed", type=int, help="Optionaler Zufalls-Seed fuer Tests")
    parser.add_argument("--output", help="Optionaler TXT-Exportpfad")
    parser.add_argument("--debug", action="store_true", help="Debug-Logging aktivieren")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    mode = ask_mode(args.mode)
    anzahl = ask_anzahl(args.anzahl)
    json_path = Path(args.json)

    if not json_path.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {json_path}")

    gruppen = load_reimgruppen(json_path)
    if not gruppen:
        raise ValueError("Keine Reimgruppen gefunden.")

    rnd = random.Random(args.seed)
    sprueche = []
    for _ in range(anzahl):
        gruppe = rnd.choice(gruppen)
        sprueche.append(generate_spruch(gruppe, mode, rnd=rnd, debug=args.debug))

    for index, spruch in enumerate(sprueche, start=1):
        print(f"\n{index}.")
        print(spruch)

    if args.output:
        ziel = export_to_txt(sprueche, args.output)
        print(f"\nGespeichert: {ziel}")


if __name__ == "__main__":
    main()
