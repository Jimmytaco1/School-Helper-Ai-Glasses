#!/usr/bin/env python3
"""evaluate_reading.py - measure how accurately the model reads YOUR handwriting.

Put pairs of files in a folder: a photo and a text file with the correct
transcription, sharing a name:

    handwriting/test/page_01.jpg
    handwriting/test/page_01.txt      <- what the page really says

Then run (with Ollama running):

    python tools/evaluate_reading.py handwriting/test
    python tools/evaluate_reading.py handwriting/test --out results.md

It prints the character error rate (CER) for each page and overall:
    CER = (characters that must be inserted, deleted or changed to turn the
           model's reading into the correct text) / (length of the correct text)
Lower is better; 0% is a perfect reading.

Keep the test pages separate from any examples you give the model, and re-run
after every change (better photos, new prompt, new model) to see what helped.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def normalise(text):
    return " ".join(text.split())


def edit_distance(a, b):
    """Levenshtein distance: the fewest single-character edits turning a into b."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1,                # delete
                           cur[j - 1] + 1,             # insert
                           prev[j - 1] + (ca != cb)))  # change
        prev = cur
    return prev[-1]


def cer(reference, hypothesis):
    ref, hyp = normalise(reference), normalise(hypothesis)
    return edit_distance(ref, hyp) / max(1, len(ref))


def find_pairs(folder):
    folder = Path(folder)
    pairs = []
    for img in sorted(folder.iterdir()):
        if img.suffix.lower() in IMAGE_EXT and img.with_suffix(".txt").exists():
            pairs.append((img, img.with_suffix(".txt")))
    return pairs


def evaluate(folder, reader=None):
    """Returns a list of dicts, one per page. `reader` turns an image path into
    text; by default it is the real model."""
    if reader is None:
        import notebook_checker as nc
        reader = nc.read_image
    results = []
    for img, txt in find_pairs(folder):
        truth = txt.read_text(encoding="utf-8")
        guess = reader(img)
        ref = normalise(truth)
        results.append({"file": img.name, "chars": len(ref),
                        "edits": edit_distance(ref, normalise(guess)),
                        "cer": cer(truth, guess)})
    return results


def overall(results):
    chars = sum(r["chars"] for r in results)
    edits = sum(r["edits"] for r in results)
    return edits / max(1, chars)


def report(results, model="(unknown)"):
    lines = ["# Handwriting reading accuracy", "",
             f"Model: `{model}`  |  Date: {dt.date.today().isoformat()}  |  Pages: {len(results)}", "",
             "| Page | Characters | Edits | CER |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['file']} | {r['chars']} | {r['edits']} | {r['cer']:.1%} |")
    lines += ["", f"**Overall CER: {overall(results):.1%}** "
              "(total edits divided by total characters; lower is better)"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="folder of page.jpg + page.txt pairs")
    ap.add_argument("--out", help="also save a markdown report to this file")
    a = ap.parse_args()
    if not find_pairs(a.folder):
        sys.exit("No image + .txt pairs found in that folder.")
    import notebook_checker as nc
    results = evaluate(a.folder)
    text = report(results, nc.MODEL)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nSaved to {a.out}")


if __name__ == "__main__":
    main()
