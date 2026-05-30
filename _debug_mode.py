import json
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"
RESCRAPE_V2_WORDS_FILE = OUTPUT_DIR / "rescrape_v1_full.json"
SNOWBALL_FRONTIER_FILE = OUTPUT_DIR / "sn_snowball_frontier.json"

print(f"OUTPUT_DIR = {OUTPUT_DIR}")
print(f"OUTPUT_DIR.exists() = {OUTPUT_DIR.exists()}")
print(f"RESCRAPE_V2_WORDS_FILE = {RESCRAPE_V2_WORDS_FILE}")
print(f"RESCRAPE_V2_WORDS_FILE.exists() = {RESCRAPE_V2_WORDS_FILE.exists()}")
print(f"SNOWBALL_FRONTIER_FILE.exists() = {SNOWBALL_FRONTIER_FILE.exists()}")

use_rescrape_v2 = RESCRAPE_V2_WORDS_FILE.exists()
use_snowball = not use_rescrape_v2 and SNOWBALL_FRONTIER_FILE.exists()

print(f"\nuse_rescrape_v2 = {use_rescrape_v2}")
print(f"use_snowball = {use_snowball}")

if use_rescrape_v2:
    mode = "rescrape_v2"
elif use_snowball:
    mode = "snowball"
else:
    mode = "unknown"

print(f"mode = {mode}")

ctrl_file = OUTPUT_DIR / "sn_rescrape_control.json"
print(f"\nsn_rescrape_control.json exists = {ctrl_file.exists()}")
if ctrl_file.exists():
    with open(ctrl_file, "r", encoding="utf-8") as f:
        ctrl = json.load(f)
    print(f"control = {json.dumps(ctrl, indent=2, ensure_ascii=False)}")

prog_file = OUTPUT_DIR / "sn_rescrape_progress.json"
print(f"\nsn_rescrape_progress.json exists = {prog_file.exists()}")
if prog_file.exists():
    with open(prog_file, "r", encoding="utf-8") as f:
        prog = json.load(f)
    print(f"progress = {json.dumps(prog, indent=2, ensure_ascii=False)}")
