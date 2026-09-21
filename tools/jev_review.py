#!/usr/bin/env python3
"""JEV review harness — use JEV Zen as engineering review layer.

Builds a TypeSafe /v1/systemone payload from a review context + questions,
posts it to JEV, and prints + stores the verdict.

JEV is ADVISORY ONLY. It must NEVER decide RNG, results, state, balances
or settlement. Every JEV flag must be validated against BRD/SRS and code
before acting on it.

Usage:
  python3 tools/jev_review.py --title req-analysis \\
      --context-file tools/jev_reviews/req_context.md \\
      --questions-file tools/jev_reviews/req_questions.json \\
      --url http://127.0.0.1:8080
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JEV_URL = os.environ.get("JEV_URL", "http://127.0.0.1:8080")
JEV_API_KEY = os.environ.get("JEV_API_KEY", "local-dev")


def build_payload(title, context, questions):
    """questions: list of {key, type, instructions, criteria?} -> map per contract."""
    qmap = {}
    for q in questions:
        entry = {"type": q["type"], "instructions": q.get("instructions", q["key"])}
        if "criteria" in q:
            entry["criteria"] = q["criteria"]
        qmap[q["key"]] = entry
    return {
        "model": "jev-latest",
        "state": f"REVIEW: {title}\n\nCONTEXT (BRD/SRS + audit + design under review):\n{context}",
        "questions": qmap,
    }


def post(payload, url):
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/systemone",
        json.dumps(payload, ensure_ascii=False).encode(),
        {"Content-Type": "application/json",
         "Authorization": "Bearer " + JEV_API_KEY},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.load(resp)
    return result, (time.perf_counter() - started) * 1000


def render(result, elapsed_ms, questions):
    print(f"=== JEV review: {result.get('model')} ({elapsed_ms:.0f} ms) ===")
    flags = []
    for key, ans in result.get("answers", {}).items():
        if ans["type"] == "noul":
            print(f"[noul ] {key}: P(true)={ans['noul']:.3f}")
            if ans["noul"] >= 0.65:
                flags.append((key, ans["noul"]))
        elif ans["type"] == "choice":
            top = ans["choice"]
            print(f"[choice] {key}: {top} "
                  + str({k: round(v, 3) for k, v in ans["probabilities"].items()}))
        elif ans["type"] == "score":
            print(f"[score] {key}: E={ans['score']:.2f} "
                  + str({k: round(v, 3) for k, v in ans["probabilities"].items()}))
            if ans["score"] >= 1.5:
                flags.append((key, ans["score"]))
    print(f"\n--- {len(flags)} flag(s) >= threshold (ADVISORY — validate before acting) ---")
    for key, v in sorted(flags, key=lambda x: -x[1]):
        print(f"  FLAG {key} ({v:.3f})")
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--title", required=True)
    ap.add_argument("--context-file", type=Path, required=True)
    ap.add_argument("--questions-file", type=Path, required=True)
    ap.add_argument("--url", default=JEV_URL)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    context = args.context_file.read_text()
    questions = json.loads(args.questions_file.read_text())
    payload = build_payload(args.title, context, questions)
    try:
        result, elapsed = post(payload, args.url)
    except Exception as exc:
        sys.exit(f"JEV review FAILED (JEV unavailable, not a code verdict): {exc}")
    flags = render(result, elapsed, [q.get("key", q) if isinstance(q, dict) else q
                                     for q in questions])
    out = args.out or (ROOT / "tools/jev_reviews" / (args.title + ".result.json"))
    out.write_text(json.dumps({"title": args.title, "elapsed_ms": elapsed,
                               "result": result,
                               "flags": flags}, ensure_ascii=False, indent=2) + "\n")
    print(f"\nStored: {out}")


if __name__ == "__main__":
    main()
