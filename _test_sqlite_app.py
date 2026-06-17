#!/usr/bin/env python3
"""Test-Skript: App-Start mit SQLite, API-Stichproben, RAM-Messung."""
import json
import os
import psutil
import sys
import time
import requests

PROC = psutil.Process(os.getpid())


def mem_mb():
    return round(PROC.memory_info().rss / 1024 / 1024, 1)


def test_sqlite_direct():
    """Test 1: SQLite direkt — Such- und Topic-Queries."""
    print("=" * 60)
    print("  TEST 1: SQLite Direkt-Zugriff")
    print("=" * 60)

    sys.path.insert(0, ".")
    # Importiere app-Modul (nicht app.run!)
    import app as appmod

    mem0 = mem_mb()
    t0 = time.time()
    ok = appmod._load_v12()
    t_load = round(time.time() - t0, 2)
    mem1 = mem_mb()
    print(f"  _load_v12(): {ok} in {t_load}s")
    print(f"  RAM: {mem0} MB -> {mem1} MB (Delta: +{round(mem1 - mem0, 1)} MB)")
    assert ok, "SQLite konnte nicht geladen werden"

    # 3 Stichproben: search
    print("\n  --- /api/sprachnudel/search Stichproben ---")
    test_words = ["Maus", "Haus", "Sonne"]
    for w in test_words:
        entry = appmod._get_v12_word(w)
        if entry:
            print(f"  '{w}': suchwort='{entry.get('suchwort')}' "
                  f"reim_count={entry.get('reim_count')} "
                  f"themen={entry.get('themen')}")
        else:
            print(f"  '{w}': NICHT GEFUNDEN")

    # Topic search
    print("\n  --- /api/sprachnudel/topic-search ---")
    cur = appmod._v12_db.cursor()
    cur.execute(
        "SELECT thema, COUNT(*) as cnt FROM themen "
        "WHERE LOWER(thema) LIKE '%tier%' GROUP BY thema ORDER BY cnt DESC LIMIT 3"
    )
    for r in cur.fetchall():
        print(f"  Thema: '{r[0]}' ({r[1]} Wörter)")

    # Topics list
    print("\n  --- /api/sprachnudel/topics ---")
    cur.execute(
        "SELECT thema, COUNT(*) as cnt FROM themen "
        "GROUP BY thema ORDER BY cnt DESC LIMIT 5"
    )
    for r in cur.fetchall():
        print(f"  '{r[0]}': {r[1]} Wörter")

    print("\n  PASSED")
    return appmod


def test_app_endpoints(appmod):
    """Test 2: HTTP-Endpunkte via Flask test client."""
    print("\n" + "=" * 60)
    print("  TEST 2: HTTP-Endpunkte (Flask test client)")
    print("=" * 60)

    client = appmod.app.test_client()

    # 3 search-Calls
    for q in ["maus", "haus", "sonne"]:
        resp = client.get("/api/sprachnudel/search?q=" + q)
        data = resp.get_json()
        if data and data.get("suchwort"):
            print(f"  search '{q}': suchwort='{data['suchwort']}' "
                  f"total={data.get('total')} "
                  f"wortart={data.get('wortart')} "
                  f"themen_count={len(data.get('themen', []))} "
                  f"rhymes_count={len(data.get('rhymes', []))}")
        else:
            print(f"  search '{q}': {resp.status_code} {data}")

    # topic-search
    resp = client.get("/api/sprachnudel/topic-search?q=tier")
    data = resp.get_json()
    if data:
        print(f"  topic-search 'tier': {data.get('total_themen')} Themen, "
              f"{data.get('total_woerter')} Wörter")
    else:
        print(f"  topic-search 'tier': {resp.status_code}")

    # topics
    resp = client.get("/api/sprachnudel/topics")
    data = resp.get_json()
    if data:
        print(f"  topics: {data.get('total')} Themen")
        for t in (data.get("topics") or [])[:3]:
            print(f"    '{t['thema']}': {t['count']}")
    else:
        print(f"  topics: {resp.status_code}")

    print("\n  PASSED")


if __name__ == "__main__":
    appmod = test_sqlite_direct()
    test_app_endpoints(appmod)
    print("\n" + "#" * 60)
    print("  ALLE TESTS BESTANDEN")
    print("#" * 60)
