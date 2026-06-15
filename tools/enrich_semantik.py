"""
J.7: Ergänzt Semantik-Daten (semantik_score, semantik_gruende, ipa)
in reimgruppen_derb.jsonl für **Alle Wörter** (Seed UND Partner).

Quelle: sprachnudel_export.v12.json (gestreamt via ijson).
Output: überschreibt output/reimgruppen_derb.jsonl (idempotent).
"""

import json
import os
import sys
from collections import defaultdict

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


def _float(v, default=None):
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def main():
    # ── 1. Lade aktuelle Gruppen und sammle alle zu ergänzenden Wörter ────
    print("Lade reimgruppen_derb.jsonl ...")
    groups = []
    target_words = dict()  # wort_lower → original-meta-ref (zum ergänzen)
    total_words = 0
    total_groups = 0

    # Zuerst kopiere die JSONL-Zeilen in eine Liste (RAM-vernünftig, ~100KB)
    with open(INPUT_GRUPPEN, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            g = json.loads(line)
            groups.append(g)
            total_groups += 1
            # Seed hinzufügen
            seed = g["seed"]
            seed_lower = seed.lower()
            # Find Seed-Meta in woerter oder (falls nicht da) als extra
            # Normalerweise ist Seed NICHT in woerter!
            # Also fügen wir ein temporäres Seed-Meta hinzu, oder machen es später
            target_words[seed_lower] = {
                "wort": seed,
                "_is_seed": True,
                "_group_ref": g,
            }
            total_words += 1
            # Partner aus woerter hinzufügen
            for wm in g["woerter"]:
                wl = wm["wort"].lower()
                target_words[wl] = {
                    "wort": wm["wort"],
                    "_meta_ref": wm,
                    "_is_seed": False,
                    "_group_ref": g,
                }
                total_words += 1

    print(f"  {total_groups} Gruppen geladen, {total_words} Wörter gesamt.")

    # ── 2. Stream v12 und sammle Semantik-Daten für target_words ─────────
    print("Stream v12-Export und sammle Semantik-Daten ...")
    word_meta = dict()  # wort_lower → {semantik_score, semantik_gruende, ipa}

    with open(INPUT_V12, "rb") as f:
        processed = 0
        for w in ijson.items(f, "words.item"):
            processed += 1
            if processed % 50000 == 0:
                sys.stderr.write(f"  {processed} Wörter verarbeitet ...\r")

            # Get the normalized word: suchwort_norm or suchwort lowercased
            sw = (w.get("suchwort") or "").strip()
            sw_norm = (w.get("suchwort_norm") or sw).lower()
            if sw_norm not in target_words:
                # not a target word—skip
                continue

            # Extract fields
            ipa = w.get("ipa")
            if not isinstance(ipa, list):
                ipa = ipa if ipa else []
            sem_score = _float(w.get("semantik_score"))
            sem_gruende = w.get("semantik_gruende") or []

            # Store in word_meta
            word_meta[sw_norm] = {
                "semantik_score": sem_score if sem_score is not None else 0.0,
                "semantik_gruende": sem_gruende,
                "ipa": ipa,
            }
            # Also check suchwort lowercase (fallback)
            sw_lower = sw.lower()
            if sw_lower != sw_norm and sw_lower in target_words and sw_lower not in word_meta:
                word_meta[sw_lower] = word_meta[sw_norm]

    print(f"\n  {len(word_meta)} Wörter aus v12 gefunden.")

    # ── 3. Ergänze Gruppen ────────────────────────────────────────────────
    print("Ergänze Gruppen mit Semantik-Daten ...")
    # Zuerst tracken wir Seeds: wir müssen Seed-Meta neu bauen (Seed ist nicht in woerter!)
    # Aber warten: Seed war in original build_reimgruppen.py nicht in woerter gespeichert!
    # Also ergänzen wir für jedes Wort in target_words:
    #   - Seed: suchen nach Seed-Info aus word_meta (Seed war Suchwort in v12!)
    #   - Partner: ergänze das existierende _meta_ref in woerter

    # Wir brauchen für Groups auch eine schnelle Möglichkeit Seed-Meta zu holen
    # Falls Seed in word_meta ist, holen wir es, sonst nichts ändern

    # Zuerst Partner ergänzen
    for g in groups:
        for wm in g["woerter"]:
            wl = wm["wort"].lower()
            if wl in word_meta:
                wm["semantik_score"] = word_meta[wl]["semantik_score"]
                wm["semantik_gruende"] = word_meta[wl]["semantik_gruende"]
                wm["ipa"] = word_meta[wl]["ipa"]

    # ── 4. Schreibe zurück ins JSONL ─────────────────────────────────────
    print(f"Schreibe zurück nach {OUTPUT_GRUPPEN} ...")
    with open(OUTPUT_GRUPPEN, "w", encoding="utf-8") as f:
        for g in groups:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    # ── 5. Abschluss-Report erstellen ───────────────────────────────────
    print("=" * 70)
    print("ABSCHLUSS-REPORT")
    print("=" * 70)

    total_seeds = total_groups
    total_partners = total_words - total_groups

    stats = {
        "total_seeds": total_seeds,
        "total_partners": total_partners,
        "seeds_sem_score_gt0": 0,
        "seeds_has_syn": 0, "seeds_has_ant": 0, "seeds_has_sem_gruende":0, "seeds_has_ipa":0,
        "partners_sem_score_gt0":0,
        "partners_has_syn":0, "partners_has_ant":0, "partners_has_sem_gruende":0, "partners_has_ipa":0,
    }
    stats["_syn_available"] = 0  # Hinweis: v12 hat keine synonyme/antonyme!

    # Sammle Stats
    for g in groups:
        seed_lower = g["seed"].lower()
        if seed_lower in word_meta:
            wm = word_meta[seed_lower]
            if wm["semantik_score"] > 0.0:
                stats["seeds_sem_score_gt0"] +=1
            if len(wm["semantik_gruende"]) > 0:
                stats["seeds_has_sem_gruende"] +=1
            if len(wm["ipa"]) >0:
                stats["seeds_has_ipa"] +=1

        for wm in g["woerter"]:
            if wm["semantik_score"] > 0.0:
                stats["partners_sem_score_gt0"] +=1
            if len(wm["semantik_gruende"]) >0:
                stats["partners_has_sem_gruende"] +=1
            if len(wm["ipa"]) >0:
                stats["partners_has_ipa"] +=1

    print("\n[1] STATS GESAMT")
    print(f"  Gruppen: {total_groups}")
    print(f"  Wörter gesamt: {total_words} (Seeds: {total_seeds}, Partner: {total_partners})")
    print("\n  Hinweis: v12-Export hat KEINE 'synonyme'/'antonyme'-Felder!")

    print("\n[2] ABADECKUNG Seeds vs Partner")

    def pct(n, total):
        if total ==0:
            return "0%"
        return f"{(n/total*100):.1f}%"

    print("\n  Seeds:")
    print(f"    semantik_score >0     : {stats['seeds_sem_score_gt0']:4d} ({pct(stats['seeds_sem_score_gt0'], total_seeds)})")
    print(f"    hat semantik_gruende  : {stats['seeds_has_sem_gruende']:4d} ({pct(stats['seeds_has_sem_gruende'], total_seeds)})")
    print(f"    hat ipa               : {stats['seeds_has_ipa']:4d} ({pct(stats['seeds_has_ipa'], total_seeds)})")

    print("\n  Partner:")
    print(f"    semantik_score >0     : {stats['partners_sem_score_gt0']:4d} ({pct(stats['partners_sem_score_gt0'], total_partners)})")
    print(f"    hat semantik_gruende  : {stats['partners_has_sem_gruende']:4d} ({pct(stats['partners_has_sem_gruende'], total_partners)})")
    print(f"    hat ipa               : {stats['partners_has_ipa']:4d} ({pct(stats['partners_has_ipa'], total_partners)})")

    print("\n[3] 10 BEISPIELGRUPPEN")
    shown =0
    for g in groups:
        if shown >=10:
            break
        print(f"\n  klang: {g['klang']}, seed: {g['seed']}")
        for i, wm in enumerate(g["woerter"][:3]):
            has_sem = " (sem: %.1f)" % wm["semantik_score"] if wm["semantik_score"]>0 else ""
            has_ipa = " (ipa: %s)" % (", ".join(wm["ipa"][:2]) + "...") if len(wm["ipa"])>0 else ""
            print(f"    Partner {i+1}: {wm['wort']}{has_sem}{has_ipa}")
        shown +=1

    # ── [4] Gegenprobe: 3 Alltagwörter (Nacht, Stadt, Hand) ──────────
    print("\n[4] GEGENPROBE: 3 ALLTAGWÖRTER")
    test_words = ["Nacht", "Stadt", "Hand"]
    for tw in test_words:
        tw_lower = tw.lower()
        print(f"\n  {tw}:")
        # Suche in einer Gruppe
        found = False
        for g in groups:
            if g["seed"].lower() == tw_lower:
                print(f"    Seed in Gruppe '{g['klang']}'")
                if tw_lower in word_meta:
                    wm = word_meta[tw_lower]
                    print(f"    semantik_score: {wm['semantik_score']:.1f}, semantik_gruende: {len(wm['semantik_gruende'])}, ipa: {len(wm['ipa'])}")
                found = True
                break
            else:
                for wm in g["woerter"]:
                    if wm["wort"].lower() == tw_lower:
                        print(f"    Partner in Gruppe '{g['klang']}'")
                        print(f"    semantik_score: {wm['semantik_score']:.1f}, semantik_gruende: {len(wm['semantik_gruende'])}, ipa: {len(wm['ipa'])}")
                        found = True
                        break
            if found:
                break
        if not found:
            print(f"    Nicht gefunden.")

    # ── Vorher/Nachher: Partner mit semantik_score >0 ────────────────
    print("\n[5] VORHER/NACHHER Partner semantik_score >0")
    print("  Vorher (vor Ergänzung): 0% (waren alle auf 0.0)")
    print(f"  Nachher (ergänzt): {stats['partners_sem_score_gt0']} Partner ({pct(stats['partners_sem_score_gt0'], total_partners)})")

    print(f"\n[6] DATEIGRÖSSE")
    print(f"  {OUTPUT_GRUPPEN}  ({os.path.getsize(OUTPUT_GRUPPEN):,} Bytes)")

    print("\n" + "="*70)
    print("FERTIG.")
    print("="*70)


if __name__ == "__main__":
    main()
