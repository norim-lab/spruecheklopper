"""
J.3 korrigiert: Ergaenzt reimgruppen_derb.jsonl um IPA, Synonyme und
Definitionen fuer **alle Woerter** (Seed UND Partner).

Quelle: sprachnudel_export.v12.json (gestreamt via ijson).
- ipa         (Listen-Feld, top-level)
- synonyme    (Listen-Feld, top-level, ~23 % Abdeckung)
- definitionen (Listen-Feld, top-level, ~51 % Abdeckung)

semantik_score / semantik_gruende werden bewusst NICHT gezogen
(Datenmuell: immer 0.05 "Gleiche Wortart"). Bereits vorhandene Werte
in reimgruppen_derb.jsonl werden nicht angefasst (rein additiv).

Output: ueberschreibt output/reimgruppen_derb.jsonl (idempotent).
"""

import json
import os
import sys

try:
    import ijson
except ImportError:
    sys.stderr.write(
        "FEHLER: ijson nicht installiert. Bitte ausfuehren:\n"
        "  python -m pip install ijson\n"
    )
    sys.exit(1)


# ── Pfade ────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INPUT_V12 = os.path.join(ROOT, "output", "sprachnudel_export.v12.json")
INPUT_GRUPPEN = os.path.join(ROOT, "output", "reimgruppen_derb.jsonl")
OUTPUT_GRUPPEN = os.path.join(ROOT, "output", "reimgruppen_derb.jsonl")


def _as_list(v):
    """Stellt sicher, dass der Wert eine Liste ist (sonst [])."""
    if isinstance(v, list):
        return v
    return []


def main():
    # ── 1. Lade aktuelle Gruppen und sammle alle zu ergaenzenden Woerter ─
    print("Lade reimgruppen_derb.jsonl ...")
    groups = []
    target_words = set()  # alle lowercased Woerter (Seed + Partner)
    total_words = 0
    total_groups = 0

    with open(INPUT_GRUPPEN, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            g = json.loads(line)
            groups.append(g)
            total_groups += 1
            # Seed (steht NICHT in woerter[])
            seed_lower = g["seed"].lower()
            target_words.add(seed_lower)
            total_words += 1
            # Partner aus woerter[]
            for wm in g["woerter"]:
                target_words.add(wm["wort"].lower())
                total_words += 1

    print(f"  {total_groups} Gruppen geladen, {total_words} Woerter gesamt.")

    # ── 2. Stream v12 und sammle IPA + Synonyme + Definitionen ─────────
    print("Stream v12-Export und sammle IPA/Synonyme/Definitionen ...")
    # wort_lower → {"ipa": [...], "synonyme": [...], "definitionen": [...]}
    word_meta = dict()

    with open(INPUT_V12, "rb") as f:
        processed = 0
        for w in ijson.items(f, "words.item"):
            processed += 1
            if processed % 50000 == 0:
                sys.stderr.write(f"  {processed} Woerter verarbeitet ...\r")

            sw = (w.get("suchwort") or "").strip()
            sw_norm = (w.get("suchwort_norm") or sw).lower()
            if sw_norm not in target_words:
                sw_lower = sw.lower()
                if sw_lower not in target_words:
                    continue
                sw_norm = sw_lower

            meta = {
                "ipa": _as_list(w.get("ipa")),
                "synonyme": _as_list(w.get("synonyme")),
                "definitionen": _as_list(w.get("definitionen")),
            }
            word_meta[sw_norm] = meta

            # Fallback: suchwort lowercase ist evtl. ein anderer Key
            sw_lower = sw.lower()
            if sw_lower != sw_norm and sw_lower in target_words and sw_lower not in word_meta:
                word_meta[sw_lower] = meta

    print(f"\n  {len(word_meta)} Woerter aus v12 gefunden.")

    # ── 3. Ergaenze Gruppen ────────────────────────────────────────────
    print("Ergaenze Gruppen mit IPA/Synonymen/Definitionen ...")

    for g in groups:
        # Seed: auf Gruppen-Ebene (seed_synonyme / seed_definition)
        seed_lower = g["seed"].lower()
        seed_meta = word_meta.get(seed_lower)
        if seed_meta is not None:
            g["seed_synonyme"] = seed_meta["synonyme"]
            g["seed_definition"] = seed_meta["definitionen"]
            # Falls IPA fehlen sollte: nichts anfassen (idempotent)
        else:
            g.setdefault("seed_synonyme", [])
            g.setdefault("seed_definition", [])

        # Partner: in jedem woerter[]-Objekt ergaenzen
        for wm in g["woerter"]:
            wl = wm["wort"].lower()
            meta = word_meta.get(wl)
            if meta is not None:
                wm["ipa"] = meta["ipa"]
                wm["synonyme"] = meta["synonyme"]
                wm["definition"] = meta["definitionen"]
            else:
                wm.setdefault("ipa", [])
                wm.setdefault("synonyme", [])
                wm.setdefault("definition", [])

    # ── 4. Schreibe zurueck ins JSONL ──────────────────────────────────
    print(f"Schreibe zurueck nach {OUTPUT_GRUPPEN} ...")
    with open(OUTPUT_GRUPPEN, "w", encoding="utf-8") as f:
        for g in groups:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    # ── 5. Abschluss-Report ────────────────────────────────────────────
    print("=" * 70)
    print("ABSCHLUSS-REPORT")
    print("=" * 70)

    total_seeds = total_groups
    total_partners = total_words - total_groups

    seeds_has_syn = 0
    seeds_has_def = 0
    partners_has_syn = 0
    partners_has_def = 0

    for g in groups:
        if len(g.get("seed_synonyme", [])) > 0:
            seeds_has_syn += 1
        if len(g.get("seed_definition", [])) > 0:
            seeds_has_def += 1
        for wm in g["woerter"]:
            if len(wm.get("synonyme", [])) > 0:
                partners_has_syn += 1
            if len(wm.get("definition", [])) > 0:
                partners_has_def += 1

    def pct(n, total):
        if total == 0:
            return "0.0%"
        return f"{(n / total * 100):.1f}%"

    print(f"\n[1] STATS GESAMT")
    print(f"  Gruppen         : {total_groups}")
    print(f"  Woerter gesamt  : {total_words}  (Seeds: {total_seeds}, Partner: {total_partners})")
    print(f"  v12-Treffer     : {len(word_meta)} Woerter")

    print(f"\n[2] ABDECKUNG")
    print(f"                    {'mit synonyme':>15}  {'mit definition':>15}")
    print(f"  Seeds   ({total_seeds:4d}) :  {seeds_has_syn:>6d} ({pct(seeds_has_syn, total_seeds):>6})  "
          f"      {seeds_has_def:>6d} ({pct(seeds_has_def, total_seeds):>6})")
    print(f"  Partner ({total_partners:4d}):  {partners_has_syn:>6d} ({pct(partners_has_syn, total_partners):>6})  "
          f"      {partners_has_def:>6d} ({pct(partners_has_def, total_partners):>6})")
    gesamt_syn = seeds_has_syn + partners_has_syn
    gesamt_def = seeds_has_def + partners_has_def
    print(f"  Gesamt  ({total_words:4d}):  {gesamt_syn:>6d} ({pct(gesamt_syn, total_words):>6})  "
          f"      {gesamt_def:>6d} ({pct(gesamt_def, total_words):>6})")

    # ── [3] 10 Beispielgruppen ────────────────────────────────────────
    print(f"\n[3] 10 BEISPIELGRUPPEN")
    shown = 0
    for g in groups:
        if shown >= 10:
            break
        print(f"\n  klang: {g['klang']}, seed: {g['seed']}")
        seed_syn = g.get("seed_synonyme", [])
        seed_def = g.get("seed_definition", [])
        if seed_syn:
            print(f"    seed_synonyme   : {seed_syn[:3]}{' ...' if len(seed_syn) > 3 else ''}")
        if seed_def:
            print(f"    seed_definition : {seed_def[:1]}")
        for i, wm in enumerate(g["woerter"][:3]):
            syn = wm.get("synonyme", [])
            d = wm.get("definition", [])
            extra = ""
            if syn:
                extra += f"  syn={syn[:2]}"
            if d:
                extra += f"  def=ja"
            print(f"    Partner {i + 1}: {wm['wort']}{extra}")
        shown += 1

    # ── [4] Gegenprobe: 5 Alltagswoerter ──────────────────────────────
    print(f"\n[4] GEGENPROBE: 5 ALLTAGSWOERTER")
    test_words = ["Nacht", "Stadt", "Hand", "Geld", "Liebe"]
    for tw in test_words:
        tw_lower = tw.lower()
        print(f"\n  {tw}:")
        found = False
        # Zuerst als Seed suchen
        for g in groups:
            if g["seed"].lower() == tw_lower:
                syn = g.get("seed_synonyme", [])
                d = g.get("seed_definition", [])
                print(f"    [SEED]  gruppe='{g['klang']}'")
                print(f"    synonyme   ({len(syn)}): {syn[:5]}")
                print(f"    definition ({len(d)}): {(d[:1] if d else [])}")
                found = True
                break
        if found:
            continue
        # Sonst als Partner
        for g in groups:
            for wm in g["woerter"]:
                if wm["wort"].lower() == tw_lower:
                    syn = wm.get("synonyme", [])
                    d = wm.get("definition", [])
                    print(f"    [PARTNER] gruppe='{g['klang']}'")
                    print(f"    synonyme   ({len(syn)}): {syn[:5]}")
                    print(f"    definition ({len(d)}): {(d[:1] if d else [])}")
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"    Nicht gefunden.")

    # ── [5] Dateigroesse ──────────────────────────────────────────────
    print(f"\n[5] DATEIGROESSE")
    print(f"  {OUTPUT_GRUPPEN}  ({os.path.getsize(OUTPUT_GRUPPEN):,} Bytes)")

    print("\n" + "=" * 70)
    print("FERTIG.")
    print("=" * 70)


if __name__ == "__main__":
    main()
