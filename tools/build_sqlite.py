#!/usr/bin/env python3
"""build_sqlite.py – Konvertiert output/sprachnudel_export.v12.json
(streamend via ijson) in eine indizierte SQLite-Datenbank
output/reimdb.sqlite.

Schema:
  Tabelle woerter  – alle Woerter mit semantischen Feldern (JSON-Spalten)
  Tabelle themen    – thema → suchwort_norm Mapping (schnelle Topic-Suche)

Indizes: suchwort_norm (NOCASE), klang, haeufigkeit, themen(thema).

Aufruf:
  python tools/build_sqlite.py
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import ijson

ROOT = Path(__file__).resolve().parent.parent
V12_PATH = ROOT / "output" / "sprachnudel_export.v12.json"
DB_PATH = ROOT / "output" / "reimdb.sqlite"

SCHEMA_WOERTER = """
CREATE TABLE IF NOT EXISTS woerter (
    id             INTEGER PRIMARY KEY,
    suchwort       TEXT,
    suchwort_norm  TEXT COLLATE NOCASE,
    klang          TEXT,
    suchwort_silben INTEGER,
    reim_count     INTEGER DEFAULT 0,
    hat_reime      INTEGER DEFAULT 0,
    haeufigkeit    INTEGER,
    wortart        TEXT,
    source_url     TEXT,
    scraped_at     TEXT,
    ipa            TEXT,
    definitionen   TEXT,
    synonyme       TEXT,
    antonyme       TEXT,
    verwandte      TEXT,
    abgeleitete    TEXT,
    themen         TEXT,
    themed_rhymes  TEXT,
    rhymes         TEXT,
    kategorien     TEXT,
    raw_results    TEXT,
    toxic          INTEGER DEFAULT 0
);
"""

SCHEMA_THEMEN = """
CREATE TABLE IF NOT EXISTS themen (
    thema         TEXT,
    suchwort_norm TEXT COLLATE NOCASE,
    hat_reime     INTEGER DEFAULT 0,
    reim_count    INTEGER DEFAULT 0,
    haeufigkeit   INTEGER
);
"""

JSON_FIELDS = (
    "wortart",
    "ipa", "definitionen", "synonyme", "antonyme", "verwandte",
    "abgeleitete", "themen", "themed_rhymes", "rhymes",
    "kategorien", "raw_results",
)
SCALAR_FIELDS = (
    "suchwort", "suchwort_norm", "klang", "suchwort_silben",
    "reim_count", "hat_reime", "haeufigkeit",
    "source_url", "scraped_at",
)

INSERT_WOERTER_SQL = """
INSERT INTO woerter (
    suchwort, suchwort_norm, klang, suchwort_silben,
    reim_count, hat_reime, haeufigkeit,
    source_url, scraped_at,
    wortart, ipa, definitionen, synonyme, antonyme, verwandte,
    abgeleitete, themen, themed_rhymes, rhymes,
    kategorien, raw_results, toxic
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_THEMEN_SQL = """
INSERT INTO themen (thema, suchwort_norm, hat_reime, reim_count, haeufigkeit)
VALUES (?, ?, ?, ?, ?)
"""


def _to_json_str(val):
    """Serialisiert Python-Objekt zu JSON-String oder NULL.
    Konvertiert Decimal-Werte (aus ijson) zu float."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        val = [_convert_decimal(v) for v in val]
    elif isinstance(val, dict):
        val = {k: _convert_decimal(v) for k, v in val.items()}
    else:
        val = _convert_decimal(val)
    return json.dumps(val, ensure_ascii=False)


def _convert_decimal(v):
    """Konvertiert Decimal zu float (ijson liefert Decimal fuer Zahlen)."""
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, list):
        return [_convert_decimal(x) for x in v]
    if isinstance(v, dict):
        return {k: _convert_decimal(x) for k, x in v.items()}
    return v


def main():
    if not V12_PATH.exists():
        print("FEHLER: " + str(V12_PATH) + " nicht gefunden")
        sys.exit(1)

    size_mb = V12_PATH.stat().st_size / 1024 / 1024
    print("Konvertiere " + V12_PATH.name + " (" + str(round(size_mb, 1))
          + " MB) -> " + DB_PATH.name)
    t0 = time.time()

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute(SCHEMA_WOERTER)
    cur.execute(SCHEMA_THEMEN)

    word_count = 0
    thema_rows = []
    batch = []
    BATCH_SIZE = 1000

    with open(V12_PATH, "rb") as fh:
        for w in ijson.items(fh, "words.item"):
            row = []
            for f in SCALAR_FIELDS:
                row.append(w.get(f))
            for f in JSON_FIELDS:
                row.append(_to_json_str(w.get(f)))
            row.append(1 if w.get("toxic") else 0)
            batch.append(row)

            themen = w.get("themen") or []
            if themen:
                norm = (w.get("suchwort_norm") or "").strip()
                hat_r = 1 if w.get("hat_reime") else 0
                rc = w.get("reim_count", 0) or 0
                haeuf = w.get("haeufigkeit")
                for t in themen:
                    thema_rows.append((t, norm, hat_r, rc, haeuf))

            word_count += 1
            if len(batch) >= BATCH_SIZE:
                cur.executemany(INSERT_WOERTER_SQL, batch)
                conn.commit()
                batch.clear()
                if word_count % 10000 == 0:
                    elapsed = time.time() - t0
                    print("  " + str(word_count) + " Woerter ("
                          + str(round(elapsed, 1)) + "s)")

        if batch:
            cur.executemany(INSERT_WOERTER_SQL, batch)

        # Themen-Batches
        print("Schreibe " + str(len(thema_rows)) + " Themen-Zuordnungen...")
        for i in range(0, len(thema_rows), 1000):
            cur.executemany(INSERT_THEMEN_SQL, thema_rows[i:i + 1000])
        conn.commit()

        # Indizes
        print("Erstelle Indizes...")
        cur.execute(
            "CREATE INDEX idx_woerter_norm ON woerter(suchwort_norm COLLATE NOCASE)"
        )
        cur.execute("CREATE INDEX idx_woerter_klang ON woerter(klang)")
        cur.execute("CREATE INDEX idx_woerter_haeuf ON woerter(haeufigkeit)")
        cur.execute("CREATE INDEX idx_themen_thema ON themen(thema)")
        cur.execute(
            "CREATE INDEX idx_themen_norm ON themen(suchwort_norm COLLATE NOCASE)"
        )
        conn.commit()

    # Stats
    cur.execute("SELECT COUNT(*) FROM woerter")
    wc = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM themen")
    tc = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT thema) FROM themen")
    tdc = cur.fetchone()[0]
    db_size = DB_PATH.stat().st_size / 1024 / 1024
    elapsed = time.time() - t0

    print("\n" + "=" * 60)
    print("  KONVERTIERUNG ABGESCHLOSSEN (" + str(round(elapsed, 1)) + "s)")
    print("=" * 60)
    print("  Woerter:          " + str(wc))
    print("  Themen-Zeilen:    " + str(tc))
    print("  Unique Themen:    " + str(tdc))
    print("  DB-Groesse:       " + str(round(db_size, 1)) + " MB")
    print("=" * 60)

    # Stichprobe
    print("\nStichprobe 5 Woerter:")
    cur.execute(
        "SELECT suchwort, suchwort_norm, klang, reim_count, hat_reime, "
        "haeufigkeit, wortart FROM woerter ORDER BY RANDOM() LIMIT 5"
    )
    for row in cur.fetchall():
        print("  " + str(row))

    conn.close()


if __name__ == "__main__":
    main()
