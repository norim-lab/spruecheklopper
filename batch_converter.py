import json
import re
from pathlib import Path

_UNBETONT = re.compile(r'(?:en|e[rsm]?)$')

def extract_klang(wort):
    w = wort.lower().strip()
    if len(w) <= 3:
        return w
    w_stamm = _UNBETONT.sub('', w)
    if len(w_stamm) < 2:
        w_stamm = w
    match = re.search(r'([aeiouäöü]{1,3}[^aeiouäöü]*)$', w_stamm)
    if match:
        return match.group(1)
    return w_stamm[-3:] if len(w_stamm) >= 3 else w_stamm

def convert_to_groups(input_file, output_file):
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Fehler: {input_file} nicht gefunden.")
        return

    paare = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                paare.append(json.loads(line))

    gruppen = {}
    for paar in paare:
        w1 = paar.get("reimwort_1", "")
        w2 = paar.get("reimwort_2", "")
        suchwort = paar.get("wort", "")
        silben = paar.get("silben", 0)
        thema = paar.get("thema", "Unbekannt")
        
        # Falls die Felder im alten Format anders heißen:
        if not w1: w1 = paar.get("wort_1", "")
        if not w2: w2 = paar.get("wort_2", "")
        if not suchwort: suchwort = paar.get("suchwort", "")
        
        if not w1 or not w2 or not suchwort:
            continue

        klang = extract_klang(w1)
        key = f"{klang}_{suchwort}_{silben}"
        
        if key not in gruppen:
            gruppen[key] = {
                "klang": klang,
                "suchwort": suchwort,
                "silben": silben,
                "thema": thema,
                "woerter": set()
            }
            
        gruppen[key]["woerter"].add(w1)
        gruppen[key]["woerter"].add(w2)

    # Sets zu Listen konvertieren für JSON-Export
    for key, group in gruppen.items():
        group["woerter"] = list(group["woerter"])

    with open(output_file, "w", encoding="utf-8") as f:
        for group in gruppen.values():
            f.write(json.dumps(group, ensure_ascii=False) + "\n")
            
    print(f"Erfolgreich konvertiert: {len(paare)} Paare in {len(gruppen)} Gruppen zusammengefasst.")
    print(f"Gespeichert in: {output_file}")

if __name__ == "__main__":
    convert_to_groups("output/reimpaare.jsonl", "output/reimgruppen_raw.jsonl")
