import json

with open('output/reimgruppen.json','r',encoding='utf-8') as f:
    data = json.load(f)

print(f"GESAMT: {len(data)} Gruppen")

all_w = []
for g in data:
    for w in g.get("woerter", []):
        all_w.append(w)

# Score-Verteilung
score_dist = {}
for w in all_w:
    s = w.get("pointe_score", 0)
    score_dist[s] = score_dist.get(s, 0) + 1

print("\nPOINTE_SCORE:")
for s in sorted(score_dist.keys(), reverse=True):
    print(f"{s}: {score_dist[s]}")

# Tote Wörter
tot = sum(1 for w in all_w if not w.get("laendlich") and w.get("pointe_score",0) < 3)
print(f"\nTOTE WÖRTER: {tot}")

# Kontext Check
bad_kontext = 0
for g in data:
    k = g.get("kontext", [])
    if len(k) != 5:
        bad_kontext += 1

print(f"KONTEXT != 5: {bad_kontext}")

print("\nSTICHPROBE:")
for i, g in enumerate(data[:10]):
    top = sorted(g["woerter"], key=lambda x: x["pointe_score"], reverse=True)[:3]
    print(f"\n[{i+1}] {g['suchwort']}")
    print("Top:", [(w["wort"], w["pointe_score"]) for w in top])
    print("Kontext:", g["kontext"])
