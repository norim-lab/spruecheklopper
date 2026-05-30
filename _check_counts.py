import sqlite3
conn = sqlite3.connect("output/wiktionary/de_wiktionary_normalized.db")
cur = conn.cursor()
for t in ["antonyms", "related_terms", "derived_terms"]:
    cnt = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t}: {cnt} rows")
    if cnt > 0:
        rows = cur.execute(f"SELECT * FROM {t} LIMIT 3").fetchall()
        for r in rows:
            print(f"  {r}")
conn.close()
