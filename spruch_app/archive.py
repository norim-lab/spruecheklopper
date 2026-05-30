"""
archive.py – Dauerhaftes SQLite-Archiv fuer generierte Bauernsprueche.

Im Gegensatz zu generator_history.json (auf 50 Eintraege gedeckelt) wird
dieses Archiv NIE truncatet. Dedup erfolgt via SHA1-Hash des normalisierten
Spruchtexts (INSERT OR IGNORE).
"""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "output" / "sprueche.db"


# ── Initialisierung ────────────────────────────────────────────────────────────

def _archive_init():
    """Erstellt die Tabelle 'sprueche' falls nicht vorhanden."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sprueche (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          hash            TEXT UNIQUE,
          spruch          TEXT NOT NULL,
          format          TEXT,
          subjekt         TEXT,
          cast_json       TEXT,
          setting         TEXT,
          thema           TEXT,
          klang_gruppen   TEXT,
          reimwoerter     TEXT,
          self_score      INTEGER,
          judge_score     REAL,
          hook            TEXT,
          model           TEXT,
          kosten_usd      REAL,
          drehscheibe     TEXT,
          favorit         INTEGER DEFAULT 0,
          veroeffentlicht INTEGER DEFAULT 0,
          created_at      TEXT
        )
    """)
    # Schema-Migration: notion_page_id fuer Notion-Sync (Schritt 7)
    try:
        conn.execute("ALTER TABLE sprueche ADD COLUMN notion_page_id TEXT")
    except sqlite3.OperationalError:
        pass  # Spalte existiert bereits
    conn.commit()
    conn.close()


# ── Dedup-Hash ─────────────────────────────────────────────────────────────────

def _normalize_spruch(text):
    """Normalisiert den Spruchtext fuer Dedup-Hashing (lowercase + whitespace)."""
    text = (text or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


def _spruch_hash(text):
    """SHA1 des normalisierten Spruchtexts."""
    return hashlib.sha1(_normalize_spruch(text).encode("utf-8")).hexdigest()


# ── Hilfsfunktion fuer JSON-Felder ──────────────────────────────────────────────

def _json_or_empty(val):
    """Serialisiert Listen/Dicts als JSON-String, akzeptiert auch Strings."""
    if val is None:
        return "[]"
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


# ── Schreiben ──────────────────────────────────────────────────────────────────

def archive_spruch(result, drehscheibe=None):
    """Speichert ein Ergebnis-Dict im Archiv. Dedup via sha1-Hash.

    Gibt die neue id zurueck, oder None wenn der Spruch bereits existierte
    (INSERT OR IGNORE) bzw. kein Spruchtext vorhanden war.
    """
    spruch = result.get("spruch", "")
    if not spruch:
        return None

    _archive_init()
    h = _spruch_hash(spruch)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO sprueche
              (hash, spruch, format, subjekt, cast_json, setting, thema,
               klang_gruppen, reimwoerter, self_score, judge_score, hook,
               model, kosten_usd, drehscheibe, favorit, veroeffentlicht,
               created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        """, (
            h,
            spruch,
            result.get("format", ""),
            result.get("subjekt", ""),
            _json_or_empty(result.get("cast", [])),
            result.get("setting", result.get("szene", "")),
            result.get("thema", ""),
            _json_or_empty(result.get("klang_gruppen", [])),
            _json_or_empty(result.get("reimwoerter", [])),
            result.get("self_score", result.get("score", 0)),
            result.get("judge_score"),
            result.get("hook_vorschlag", ""),
            result.get("model", ""),
            result.get("kosten_usd", 0.0),
            drehscheibe or "",
            created,
        ))
        conn.commit()
        return cur.lastrowid if cur.rowcount > 0 else None
    finally:
        conn.close()


# ── Lesen ──────────────────────────────────────────────────────────────────────

def get_archive(limit=50, nur_favoriten=False, thema=None, min_judge=None):
    """Liefert Sprueche aus dem Archiv (neueste zuerst).

    limit:          Max. Anzahl Ergebnisse
    nur_favoriten:  Nur als Favorit markierte Sprueche
    thema:          Filter nach Thema (exakter Match)
    min_judge:      Mindest-Judge-Score
    """
    _archive_init()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM sprueche WHERE 1=1"
    params = []
    if nur_favoriten:
        query += " AND favorit = 1"
    if thema:
        query += " AND thema = ?"
        params.append(thema)
    if min_judge is not None:
        query += " AND judge_score >= ?"
        params.append(float(min_judge))
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_by_judge(n=3, min_judge=4):
    """Liefert die Top-n Sprueche nach judge_score (absteigend).
    Fuer dynamische Few-Shot-Beispiele.

    n:         Max. Anzahl Ergebnisse
    min_judge: Mindest-Judge-Score (Sprueche darunter werden ignoriert)
    """
    _archive_init()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    query = ("SELECT * FROM sprueche WHERE judge_score IS NOT NULL "
             "AND judge_score >= ? ORDER BY judge_score DESC LIMIT ?")
    rows = conn.execute(query, (float(min_judge), int(n))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_archive_count():
    """Liefert die Gesamtzahl der archivierten Sprueche."""
    _archive_init()
    conn = sqlite3.connect(str(DB_PATH))
    count = conn.execute("SELECT COUNT(*) FROM sprueche").fetchone()[0]
    conn.close()
    return count


# ── Flags setzen ───────────────────────────────────────────────────────────────

def set_favorit(id, wert):
    """Setzt das favorit-Flag (0 oder 1)."""
    _archive_init()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE sprueche SET favorit = ? WHERE id = ?",
                 (1 if wert else 0, int(id)))
    conn.commit()
    conn.close()


def set_veroeffentlicht(id, wert):
    """Setzt das veroeffentlicht-Flag (0 oder 1)."""
    _archive_init()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE sprueche SET veroeffentlicht = ? WHERE id = ?",
                 (1 if wert else 0, int(id)))
    conn.commit()
    conn.close()
