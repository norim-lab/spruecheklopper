import json
import asyncio
import aiohttp

INPUT = "output/_pre_kontext.json"
OUTPUT = "output/reimgruppen_final_v2.json"
MAX_CONCURRENT = 20

KONTEXT_MESSAGES = lambda thema, suchwort: [
    {"role": "system", "content": "Du bist Experte für deutschen Bauernalltag des 19. Jahrhunderts."},
    {"role": "user", "content": f"""Nenne 5 konkrete Gegenstände oder Lebewesen aus dem bäuerlichen Alltag um 1890. Nur echte deutsche Wörter die ein Bauer täglich sah, roch oder anfasste.

VERBOTEN:
- Fantasiewörter
- Komposita mit dem Suchwort selbst
- Abstrakte Begriffe
- Rechtsbegriffe
- Moderne Dinge

GUTE BEISPIELE:
Mistgabel, Melkeimer, Heuboden, Stalllaterne, Sense, Scheunentor, Kuhfladen, Dreschflegel, Butterfass, Pferdekummet, Tragehamen, Milchkanne, Waschzuber, Backtrog, Schürze

Thema der Reimgruppe: {thema}
Suchwort (NUR zur Orientierung, NICHT ins Ergebnis aufnehmen!): {suchwort}

Nur JSON-Array zurück:
["wort1","wort2","wort3","wort4","wort5"]"""}
]

with open(INPUT, "r", encoding="utf-8") as f:
    gruppen = json.load(f)

print(f"Kontext-Reset fuer {len(gruppen)} Gruppen...")


async def generate_kontext(session, sem, suchwort, thema, api_key):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": "glm-4-plus",
        "messages": KONTEXT_MESSAGES(thema, suchwort),
        "temperature": 0.1,
        "max_tokens": 300,
        "response_format": {"type": "json_object"}
    }
    async with sem:
        for attempt in range(3):
            try:
                async with session.post(
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    headers=headers, json=data,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 429:
                        await asyncio.sleep((attempt + 1) * 1)
                        continue
                    resp.raise_for_status()
                    result = await resp.json()
                    text = result["choices"][0]["message"]["content"]
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return parsed[:5]
                    if isinstance(parsed, dict):
                        for v in parsed.values():
                            if isinstance(v, list):
                                return v[:5]
                    return []
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep((attempt + 1) * 1)
    return []


async def main():
    import sys
    sys.path.insert(0, ".")
    from ai_evaluator import get_config

    config = get_config()
    api_key = config.get("api_key")

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for g in gruppen:
            sw = g.get("suchwort", "")
            th = g.get("thema", "")
            tasks.append(generate_kontext(session, sem, sw, th, api_key))

        results = await asyncio.gather(*tasks)

    for i, g in enumerate(gruppen):
        kontext = results[i] if results[i] else []
        valid = []
        seen = set()
        for k in kontext:
            ks = str(k).strip()
            if ks and ks not in seen and not ks.endswith("ungen"):
                seen.add(ks)
                valid.append(ks)
        g["kontext"] = valid[:5]
        print(f"  [{i+1}/{len(gruppen)}] {g.get('suchwort','')} -> {g['kontext']}")

    tw = sum(len(g.get("woerter", [])) for g in gruppen)
    print()
    print(f"Output: {len(gruppen)} Gruppen, {tw} Woerter")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(gruppen, f, ensure_ascii=False, indent=2)
    print(f"-> {OUTPUT} geschrieben")


asyncio.run(main())
