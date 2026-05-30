import sqlite3
conn = sqlite3.connect("output/wiktionary/de_wiktionary_normalized.db")
cur = conn.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
for t in tables:
    cols = cur.execute(f"PRAGMA table_info({t})").fetchall()
    print(f"\n{t}:")
    for c in cols:
        print(f"  {c[1]} ({c[2]})")
conn.close()
