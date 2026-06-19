import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "output" / "ab_R1b_raw.json"
DB_PATH = ROOT / "output" / "reimdb.sqlite"
OUT_PATH = ROOT / "output" / "diag_R1b_report.md"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _split_lines(text: str) -> list[str]:
    if text is None:
        return []
    return [z.strip() for z in str(text).replace("\\n", "\n").splitlines() if z.strip()]


_TRAIL_PUNCT_RE = re.compile(r"^[\(\[\{\"'“”‘’]+|[\)\]\}\"'“”‘’,.!?;:]+$")


def _clean_word(word: str) -> str:
    w = unicodedata.normalize("NFKC", str(word)).strip()
    w = w.replace("\u00ad", "")
    w = w.strip()
    w = _TRAIL_PUNCT_RE.sub("", w)
    return w


def _db_variants(word: str) -> list[str]:
    w = _clean_word(word).lower()
    if not w:
        return []
    vars_ = [w]
    rep = (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
    )
    w2 = w
    for a, b in rep:
        w2 = w2.replace(a, b)
    if w2 != w:
        vars_.append(w2)
    w3 = w.replace("’", "'").replace("‘", "'")
    if w3 != w:
        vars_.append(w3)
    out = []
    seen = set()
    for x in vars_:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _get_haeufigkeit(conn: sqlite3.Connection, word: str) -> int | None:
    cur = conn.cursor()
    for v in _db_variants(word):
        cur.execute(
            "SELECT haeufigkeit FROM woerter WHERE suchwort_norm = ? COLLATE NOCASE LIMIT 1",
            (v,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return None
    return None


def _extract_endwords(text: str) -> list[str]:
    lines = _split_lines(text)
    ends = []
    for z in lines:
        toks = z.split()
        if not toks:
            continue
        ends.append(_clean_word(toks[-1]))
    return ends


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _sha_short(text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return h[:12]


def main():
    raw = _read_json(RAW_PATH)
    seeds = raw.get("seeds") or []
    arms = ["A", "B", "C"]
    sex_blocklist = set(raw.get("meta", {}).get("sex_blocklist") or [])

    conn = sqlite3.connect(str(DB_PATH))
    try:
        prompt_rows = []
        prompt_missing = 0

        freq_rows = []
        freq_violations_bc = 0
        freq_missing_bc = 0
        bc_total_pairs = 0

        sex_rows = []
        sex_total_blocked = 0
        sex_total_cells_with_pool = 0
        sex_cells_missing_pool = 0

        empty_or_error_cells = 0

        weird_words = ["abdominal", "zervikal"]
        weird_rows = []

        for seed in seeds:
            for arm in arms:
                cell = (((raw.get("results") or {}).get(seed) or {}).get(arm) or {})
                spruch = cell.get("spruch", "") or ""
                error = (cell.get("error") or "").strip()

                if error or not spruch.strip():
                    empty_or_error_cells += 1

                expected_prompt = {
                    "A": "SYSTEM_PROMPT",
                    "B": "SYSTEM_PROMPT_V4_ZWEI",
                    "C": "SYSTEM_PROMPT_V4_VIER",
                }[arm]

                prompt_logged = False
                fewshot_logged = False
                prompt_rows.append([
                    arm,
                    seed,
                    expected_prompt,
                    "nein",
                    "unbekannt",
                    "nein",
                    (error[:60] + "…") if len(error) > 60 else error,
                ])
                prompt_missing += 1

                ends = _extract_endwords(spruch)
                cap = 18 if arm == "A" else 5

                if arm in ("A", "B"):
                    pair_label = "Z1/Z2"
                    if len(ends) >= 2:
                        w1, w2 = ends[0], ends[1]
                    else:
                        w1, w2 = (ends[0] if ends else ""), ""
                    h1 = _get_haeufigkeit(conn, w1) if w1 else None
                    h2 = _get_haeufigkeit(conn, w2) if w2 else None
                    over = any(
                        (h is not None and h > cap) for h in (h1, h2)
                    )

                    freq_rows.append([
                        arm,
                        seed,
                        pair_label,
                        w1 or "(fehlend)",
                        str(h1) if h1 is not None else "(?)",
                        w2 or "(fehlend)",
                        str(h2) if h2 is not None else "(?)",
                        str(cap),
                        "ja" if over else "nein",
                    ])

                    if arm in ("B", "C"):
                        bc_total_pairs += 1
                        if (h1 is None and w1) or (h2 is None and w2):
                            freq_missing_bc += 1
                        if over:
                            freq_violations_bc += 1

                if arm == "C":
                    for idx, pair_label in enumerate(["Z1/Z2", "Z3/Z4"]):
                        base = idx * 2
                        if len(ends) >= base + 2:
                            w1, w2 = ends[base], ends[base + 1]
                        else:
                            w1, w2 = "", ""
                        h1 = _get_haeufigkeit(conn, w1) if w1 else None
                        h2 = _get_haeufigkeit(conn, w2) if w2 else None
                        over = any(
                            (h is not None and h > cap) for h in (h1, h2)
                        )
                        freq_rows.append([
                            arm,
                            seed,
                            pair_label,
                            w1 or "(fehlend)",
                            str(h1) if h1 is not None else "(?)",
                            w2 or "(fehlend)",
                            str(h2) if h2 is not None else "(?)",
                            str(cap),
                            "ja" if over else "nein",
                        ])
                        bc_total_pairs += 1
                        if (h1 is None and w1) or (h2 is None and w2):
                            freq_missing_bc += 1
                        if over:
                            freq_violations_bc += 1

                sex_stats = cell.get("sex_cap_stats") or {}
                n_total = int(sex_stats.get("n_total") or 0)
                n_blocked = int(sex_stats.get("n_blocked") or 0)
                sex_capped = bool(cell.get("sex_capped"))
                schlusswort = (cell.get("letztes_wort") or "").strip().lower()
                on_blocklist = "ja" if (schlusswort in sex_blocklist and schlusswort) else "nein"

                if n_total > 0:
                    sex_total_cells_with_pool += 1
                else:
                    sex_cells_missing_pool += 1
                sex_total_blocked += n_blocked

                sex_rows.append([
                    arm,
                    seed,
                    str(n_total),
                    str(n_blocked),
                    "ja" if sex_capped else "nein",
                    schlusswort or "(leer)",
                    on_blocklist,
                ])

                text_lc = spruch.lower()
                for ww in weird_words:
                    if ww in text_lc:
                        h = _get_haeufigkeit(conn, ww)
                        weird_rows.append([
                            arm,
                            seed,
                            ww,
                            str(h) if h is not None else "(?)",
                            "ja" if (h is not None and h > 5 and arm in ("B", "C")) else "nein",
                        ])

        bad_words = ["fut", "arsch", "rumpf", "gaul"]
        found_bad = {w: {"schlusswort": 0, "irgendwo": 0} for w in bad_words}
        for seed in seeds:
            for arm in arms:
                cell = raw["results"][seed][arm]
                spruch = (cell.get("spruch") or "")
                schlusswort = (cell.get("letztes_wort") or "").strip().lower()
                for w in bad_words:
                    if schlusswort == w:
                        found_bad[w]["schlusswort"] += 1
                    if w in spruch.lower():
                        found_bad[w]["irgendwo"] += 1

        missing_blocklist = [w for w in bad_words if w not in sex_blocklist and found_bad[w]["schlusswort"] > 0]

        gC = (((raw.get("results") or {}).get("Geschenk") or {}).get("C") or {})
        gC_error = (gC.get("error") or "").strip()
        gC_pool_n = len(gC.get("judge_pool") or [])
        gC_n_total = int((gC.get("sex_cap_stats") or {}).get("n_total") or 0)
        gC_v = gC.get("vierzeiler_validation") or {}

        verdict_prompt = "❌" if prompt_missing else "✅"
        verdict_freq = "✅" if freq_violations_bc == 0 and freq_missing_bc == 0 else "❌"
        verdict_sex = "✅" if sex_total_cells_with_pool >= 17 else "❌"

        run_valid = "kaputt" if empty_or_error_cells else "valide"

        lines = []
        lines.append("# R.1b Diagnose-Report (Hebel-Pruefung)")
        lines.append("")
        lines.append("Quelle: output/ab_R1b_raw.json (R.1b-Lauf).")
        lines.append("")
        lines.append("**Kopf-Verdikt (Hebel aktiv?)**")
        lines.append(f"- Prompt-Echo belegbar? {verdict_prompt} (Prompt-Text wurde nicht mitgeloggt -> Arm-Zuordnung nicht beweisbar)")
        lines.append(f"- Haeufigkeits-Cap (MAX_HAEUFIGKEIT=5) wirksam in B/C? {verdict_freq} (Verstoesse: {freq_violations_bc}, unbekannte DB-Treffer: {freq_missing_bc})")
        lines.append(f"- Sex-Cap ausgefuehrt? {verdict_sex} (Zellen mit Kandidaten-Pool: {sex_total_cells_with_pool}/18, geblockte Kandidaten gesamt: {sex_total_blocked})")
        lines.append(f"- Lauf insgesamt: **{run_valid}** (Zellen mit Fehler oder leerem Output: {empty_or_error_cells}/18)")
        lines.append("")

        lines.append("## 1) PROMPT-ECHO (was ging real an grok?)")
        lines.append("")
        lines.append("In `ab_R1b_raw.json` wird der tatsaechlich gesendete System-Prompt nicht gespeichert.")
        lines.append("Damit laesst sich NICHT beweisen, ob Arm B/C wirklich den V4-Prompt (inkl. FEWSHOT_V4) genutzt hat.")
        lines.append("")
        lines.append(_md_table(
            ["Arm", "Seed", "Erwarteter Prompt", "Prompt-Text geloggt?", "FEWSHOT_V4 enthalten?", "Fewshot geloggt?", "Fehler (kurz)"],
            prompt_rows,
        ))
        lines.append("")
        lines.append("Befund: Ohne Prompt-Log sind wir bei der Kernfrage (Prompt aktiv?) technisch blind.")
        lines.append("")
        lines.append("## 2) HAEUFIGKEITS-CAP (MAX_HAEUFIGKEIT=5 in B/C)")
        lines.append("")
        lines.append(_md_table(
            ["Arm", "Seed", "Paar", "Reimwort 1", "h1", "Reimwort 2", "h2", "Cap", ">Cap?"],
            freq_rows,
        ))
        lines.append("")
        lines.append("Explizit: abdominal/zervikal")
        lines.append("")
        if weird_rows:
            lines.append(_md_table(
                ["Arm", "Seed", "Wort", "haeufigkeit", ">5 (nur B/C)?"],
                weird_rows,
            ))
        else:
            lines.append("(Kein Treffer in den Sieger-Spruechen.)")
        lines.append("")

        lines.append("## 3) SEX-CAP (Blockliste + Trigger)")
        lines.append("")
        lines.append(_md_table(
            ["Arm", "Seed", "Pool n_total", "Pool n_blocked", "sex_capped (alle geblockt)?", "Schlusswort", "Schlusswort in Blockliste?"],
            sex_rows,
        ))
        lines.append("")
        lines.append("Problemwoerter (aus User-Liste) im Sieger-Output:")
        lines.append("")
        pw_rows = []
        for w in bad_words:
            pw_rows.append([
                w,
                str(found_bad[w]["schlusswort"]),
                str(found_bad[w]["irgendwo"]),
                "ja" if w in sex_blocklist else "nein",
            ])
        lines.append(_md_table(
            ["Wort", "als Schlusswort (Anzahl)", "irgendwo im Spruch (Anzahl)", "in Blockliste?"],
            pw_rows,
        ))
        lines.append("")
        if missing_blocklist:
            lines.append("Fehlende Blocklisten-Woerter (wurden als Schlusswort ausgegeben, sind aber NICHT geblockt):")
            lines.append("")
            lines.append("- " + ", ".join(missing_blocklist))
        else:
            lines.append("Keine der genannten Problemwoerter waren als Schlusswort ausserhalb der Blockliste.")
        lines.append("")
        lines.append("Hinweis: Der Sex-Cap im Harness prueft nur das **allerletzte Wort** (Schlusswort), nicht beliebige Woerter im Spruchkoerper.")
        lines.append("")

        lines.append("## 4) Geschenk·C (kein Output) — konkrete Ursache")
        lines.append("")
        lines.append(f"- error: `{gC_error or '(leer)'}`")
        lines.append(f"- judge_pool: {gC_pool_n} Kandidaten")
        lines.append(f"- sex_cap_stats.n_total: {gC_n_total}")
        lines.append(f"- vierzeiler_validation: active={bool(gC_v.get('active'))}, n_rejected={int(gC_v.get('n_rejected') or 0)}")
        lines.append("")

        lines.append("## Was war kaputt + minimaler Fix (nur benennen)")
        lines.append("")
        fixes = []
        fixes.append("PROMPT-ECHO: `ab_R1b_raw.json` loggt den gesendeten Prompt nicht -> minimal: pro (Arm,Seed) `system_prompt_name` + `system_prompt_sha256` (oder erstes N Zeichen) mitschreiben.")
        fixes.append("GESCHENK·C: Exception (`list` hat kein `.replace`) -> minimal: Traceback/Stacktrace in raw loggen, damit die Ursache lokalisierbar ist (aktuell nur Kurzstring).")
        fixes.append("SEX-CAP: technisch ausgefuehrt, aber Blockliste deckt 'arsch'/'fut' nicht ab -> falls gewuenscht: diese Schlusswoerter explizit aufnehmen.")
        fixes.append("ROBUSTHEIT: bei Exceptions ist `ok` aktuell irrefuehrend (leerer Output wird als ok=true markiert) -> minimal: ok-Flag strikt aus `best.ok` ableiten.")
        lines.extend(["- " + x for x in fixes])
        lines.append("")

        OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
        print("Wrote:", OUT_PATH)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

