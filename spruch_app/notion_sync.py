"""
notion_sync.py – Schiebt veroeffentlichte Sprueche (veroeffentlicht=1) in eine
Notion-Datenbank. Idempotent: jeder Spruch wird nur einmal gepusht, die
zurueckgegebene notion_page_id wird zurueckgeschrieben.

Voraussetzungen in config.json:
  notion_token:  Token der internen Notion-Integration
  notion_db_id:  32-stellige Hash-ID der Ziel-Datenbank
"""

import json
import sqlite3
from pathlib import Path

import requests

NOTION_API = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"
DB_PATH = Path(__file__).parent.parent / "output" / "sprueche.db"


def _cfg():
    """Liest notion_token und notion_db_id aus config.json.
    Wirft KeyError, wenn die Schluessel fehlen."""
    cfg = json.load(
        open(Path(__file__).parent.parent / "config.json", encoding="utf-8")
    )
    token = cfg.get("notion_token", "")
    db_id = cfg.get("notion_db_id", "")
    if not token or not db_id:
        raise ValueError(
            "notion_token und/oder notion_db_id fehlen in config.json"
        )
    return token, db_id


def _headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rt(text):
    """Baut ein rich_text-Element, beschneidet auf 2000 Zeichen (Notion-Limit)."""
    text = (text or "")[:2000]
    return [{"text": {"content": text}}]


def _props(row):
    """Mappt eine sqlite3.Row auf Notion-Properties."""
    cast = json.loads(row["cast_json"] or "[]") if row["cast_json"] else []
    themen = [
        t.strip()
        for t in (row["thema"] or "").split(",")
        if t.strip()
    ]

    p = {
        "Spruch": {
            "title": [{"text": {"content": (row["spruch"] or "")[:2000]}}]
        },
        "Status": {"select": {"name": "Veröffentlicht"}},
    }

    # Optionale select-Properties (nur setzen wenn Wert vorhanden)
    for key, src in [
        ("Format", "format"),
        ("Setting", "setting"),
        ("Modell", "model"),
    ]:
        val = row[src] if src in row.keys() else ""
        if val:
            p[key] = {"select": {"name": val}}

    if cast:
        p["Cast"] = {"multi_select": [{"name": c} for c in cast]}
    if themen:
        p["Themen"] = {"multi_select": [{"name": t} for t in themen]}

    # Number-Properties
    for key, src in [
        ("Self-Score", "self_score"),
        ("Judge-Score", "judge_score"),
        ("Kosten (USD)", "kosten_usd"),
        ("DB-ID", "id"),
    ]:
        val = row[src] if src in row.keys() else None
        if val is not None:
            try:
                p[key] = {"number": float(val) if src == "kosten_usd" or src == "judge_score" else int(val)}
            except (TypeError, ValueError):
                pass

    # Rich-Text-Properties
    for key, src in [
        ("Hash", "hash"),
        ("Hook", "hook"),
        ("Reimwoerter", "reimwoerter"),
        ("Klang-Gruppen", "klang_gruppen"),
        ("Drehscheibe", "drehscheibe"),
    ]:
        val = row[src] if src in row.keys() else ""
        if val:
            p[key] = {"rich_text": _rt(val)}

    # Checkbox
    p["Favorit"] = {"checkbox": bool(row["favorit"])}

    # Date (ISO-Format)
    created = row["created_at"] if "created_at" in row.keys() else ""
    if created:
        # Notion erwartet YYYY-MM-DD oder ISO 8601
        p["Generiert am"] = {"date": {"start": created[:19]}}

    return p


def sync_one(row, token, db_id):
    """Pusht einen einzelnen Spruch nach Notion, gibt die Page-ID zurueck."""
    body = {"parent": {"database_id": db_id}, "properties": _props(row)}
    r = requests.post(NOTION_API, headers=_headers(token), json=body, timeout=20)
    r.raise_for_status()
    return r.json()["id"]


def sync_pending():
    """Schiebt alle veroeffentlicht=1-Sprueche ohne notion_page_id nach Notion.

    Gibt (synced, errors) zurueck: Anzahl erfolgreicher Syncs + Liste der
    Fehlermeldungen.
    """
    token, db_id = _cfg()

    # Sicherstellen, dass die Tabelle + Spalte existieren
    from spruch_app import archive
    archive._archive_init()

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM sprueche WHERE veroeffentlicht=1 "
        "AND (notion_page_id IS NULL OR notion_page_id='')"
    ).fetchall()

    synced = 0
    errors = []
    for row in rows:
        try:
            page_id = sync_one(dict(row), token, db_id)
            con.execute(
                "UPDATE sprueche SET notion_page_id=? WHERE id=?",
                (page_id, row["id"]),
            )
            con.commit()
            synced += 1
        except Exception as e:
            errors.append("DB-ID " + str(row["id"]) + ": " + str(e))

    con.close()
    return synced, errors
