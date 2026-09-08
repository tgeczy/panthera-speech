"""Check Android's text fixtures against the desktop rules; --write regenerates.

Only generated text is used. No Android device or engine data is required.
"""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    source = ROOT / "panthera/addon/synthDrivers/_panthera/pantheraabbrev.py"
    spec = importlib.util.spec_from_file_location("abbreviations", source)
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    texts = list(rules.ACRONYMS) + list(rules.TITLES) + [
        "II", "XIV", "MIX", "CIVIL", "MCM", "MMXXVI", "X's", "Space X’s",
        "Dr. Kirk Heilbrun", "Mulholland Dr. is long", "4mm", "4 m", "4kg",
        "1,234MB", "20ish", "dr st vs", "DR. ST.", "éDRé", "5KB",
    ]
    rows = []
    for token in texts:
        for text in [token, "Before " + token + " after.", token + "."]:
            for expand in [False, True]:
                expected = rules.disambiguate(text, expand)
                if not expand:
                    expected = rules.spell(expected)
                rows.append(dict(text=text, expand=expand, expected=expected))
    target = ROOT / "src/platforms/android/app/src/androidTest/assets/abbreviation-oracle.json"
    output = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8")
    elif target.read_text(encoding="utf-8") != output:
        raise SystemExit("Android abbreviation fixtures differ; regenerate with --write")
    print(f"{len(rows)} Android abbreviation fixtures agree with the desktop rules")


if __name__ == "__main__":
    main()
