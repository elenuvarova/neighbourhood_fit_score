"""
Week 8 — Offline narrative batch (Groq free tier).

Generates a 2-3 sentence neighbourhood summary for every sector × scenario
using Groq's API (OpenAI-compatible, llama-3.3-70b-versatile free tier).

Prerequisites:
    pip install openai  (requirements-pipeline.txt)
    GROQ_API_KEY set in backend/pipeline/.env  (get at console.groq.com)

Input:  processed/scores.csv
        processed/sectors.geojson
Output: processed/narratives.csv

Cost: free tier (llama-3.3-70b-versatile)
      Free tier rate limits apply — concurrency is capped at 10 concurrent requests.
      ~2172 requests complete in ~5-10 minutes on the free tier.

Idempotent: sectors already in narratives.csv are skipped on re-run.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError

load_dotenv(Path(__file__).parent / ".env")

from config import DATA_PROCESSED, SCENARIO_WEIGHTS  # noqa: E402

SCORES_CSV      = DATA_PROCESSED / "scores.csv"
SECTORS_GEOJSON = DATA_PROCESSED / "sectors.geojson"
NARRATIVES_CSV  = DATA_PROCESSED / "narratives.csv"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MODEL         = "llama-3.3-70b-versatile"
MAX_TOKENS    = 256
CONCURRENCY   = 3   # Groq free tier: 30 RPM, keep well under to avoid backoffs

CATEGORY_LABELS: dict[str, str] = {
    "school":      "schools",
    "childcare":   "childcare facilities",
    "playground":  "playgrounds",
    "park":        "parks",
    "pharmacy":    "pharmacies",
    "gp":          "GP clinics",
    "hospital":    "hospitals",
    "supermarket": "supermarkets",
    "convenience": "local shops",
    "transit":     "public transport",
    "cafe":        "cafés",
    "restaurant":  "restaurants",
    "coworking":   "coworking spaces",
    "library":     "libraries",
    "sport":       "sports facilities",
}

SCENARIO_LABELS: dict[str, str] = {
    "family": "families with children",
    "senior": "older adults",
    "remote": "remote workers",
}

SYSTEM_PROMPT = """\
You write concise neighbourhood summaries for people considering relocating to Brussels.

Rules you MUST follow:
1. Use ONLY the facts in the user message — no external knowledge or invented numbers.
2. Do not mention demographics, income, ethnicity, or crime.
3. Write exactly 2-3 sentences in British English, present tense.
4. Mention 1-2 specific strengths (score ≥ 70) and at most 1 gap (score ≤ 30).
5. Use the human-readable category labels from the facts, not internal codes.
6. Return valid JSON only — no markdown fences, no explanation outside the JSON.

Output format (JSON only):
{"narrative": "...", "highlights": [{"label": "...", "kind": "pro"}, {"label": "...", "kind": "con"}]}
"""


def _load_sector_names() -> dict[str, str]:
    with open(SECTORS_GEOJSON) as f:
        fc = json.load(f)
    out: dict[str, str] = {}
    for feat in fc["features"]:
        p = feat["properties"]
        sid = p.get("CD_SECTOR") or p.get("id") or ""
        name = p.get("TX_SECTOR_DESCR_FR") or p.get("name_fr") or sid
        out[str(sid)] = str(name)
    return out


def _load_scores() -> list[dict]:
    with open(SCORES_CSV, newline="") as f:
        return list(csv.DictReader(f))


def _load_existing() -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if not NARRATIVES_CSV.exists():
        return done
    with open(NARRATIVES_CSV, newline="") as f:
        for row in csv.DictReader(f):
            done.add((row["sector_id"], row["scenario"]))
    return done


def _build_user_message(row: dict, sector_name: str) -> str:
    breakdown = json.loads(row.get("breakdown") or "{}")
    scenario  = row["scenario"]
    weights   = SCENARIO_WEIGHTS.get(scenario, {})
    overall   = int(float(row.get("score", 0)) * 100)

    facts = []
    for cat, raw in sorted(breakdown.items(), key=lambda kv: -abs(float(kv[1]))):
        if cat not in weights:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        score_int = round(float(raw) * 100)
        facts.append({"category_label": label, "score": score_int})

    return json.dumps({
        "sector_name":  sector_name,
        "scenario":     SCENARIO_LABELS.get(scenario, scenario),
        "overall_score": overall,
        "facts":        facts,
    }, ensure_ascii=False)


def _validate(text: str) -> dict | None:
    try:
        # Strip any accidental markdown fences
        clean = re.sub(r"```(?:json)?|```", "", text).strip()
        obj = json.loads(clean)
        if not isinstance(obj.get("narrative"), str) or not obj["narrative"].strip():
            return None
        if not isinstance(obj.get("highlights"), list):
            obj["highlights"] = []
        # Reject numbers > 100 (model invented a statistic not in facts)
        for d in re.findall(r"\b\d+\b", obj["narrative"]):
            if int(d) > 100:
                return None
        return obj
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def _generate_one(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    row: dict,
    sector_name: str,
) -> dict:
    """Returns {"sector_id", "scenario", "narrative", "highlights_json"} or empty narrative on failure."""
    user_msg = _build_user_message(row, sector_name)

    for attempt in range(4):
        async with sem:
            try:
                resp = await client.chat.completions.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    temperature=0.3,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                )
                text = resp.choices[0].message.content or ""
                parsed = _validate(text)
                if parsed:
                    await asyncio.sleep(2)  # 2 s throttle: 3 concurrent × 0.5 req/s ≈ 6 RPM < 30 RPM limit
                    return {
                        "sector_id":      row["sector_id"],
                        "scenario":       row["scenario"],
                        "narrative":      parsed["narrative"],
                        "highlights_json": json.dumps(parsed["highlights"], ensure_ascii=False),
                    }
                # Validation failed — fall through to empty
                break
            except RateLimitError:
                wait = 2 ** attempt * 15  # 15, 30, 60, 120 s
                await asyncio.sleep(wait)
            except Exception:
                break

    return {
        "sector_id":       row["sector_id"],
        "scenario":        row["scenario"],
        "narrative":       "",
        "highlights_json": "[]",
    }


async def _run(pending: list[dict], sector_names: dict[str, str]) -> list[dict]:
    api_key = os.environ.get("GROQ_API_KEY", "")
    client  = AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
    sem     = asyncio.Semaphore(CONCURRENCY)

    tasks = [
        _generate_one(client, sem, row, sector_names.get(row["sector_id"], row["sector_id"]))
        for row in pending
    ]

    results = []
    done_count = 0
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        done_count += 1
        if done_count % 50 == 0 or done_count == len(tasks):
            print(f"  {done_count}/{len(tasks)} done", end="\r", flush=True)

    print()
    return results


def main() -> None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set. Add it to backend/pipeline/.env")
        print("       Get a free key at console.groq.com")
        sys.exit(1)

    for path, label in [(SCORES_CSV, "scores.csv"), (SECTORS_GEOJSON, "sectors.geojson")]:
        if not path.exists():
            print(f"ERROR: {path} not found. Run pipeline steps 02 and 05 first.")
            sys.exit(1)

    sector_names = _load_sector_names()
    all_rows     = _load_scores()
    done         = _load_existing()

    pending = [r for r in all_rows if (r["sector_id"], r["scenario"]) not in done]
    print(f"Total: {len(all_rows)} | Already done: {len(done)} | Pending: {len(pending)}")

    if not pending:
        print("All narratives already generated.")
        return

    print(f"Generating {len(pending)} narratives with {MODEL} (concurrency={CONCURRENCY})…")
    results = asyncio.run(_run(pending, sector_names))

    ok = sum(1 for r in results if r["narrative"])
    failed = len(results) - ok
    print(f"Succeeded: {ok}  |  Fallbacks (empty): {failed}")

    write_header = not NARRATIVES_CSV.exists()
    with open(NARRATIVES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["sector_id", "scenario", "narrative", "highlights_json"]
        )
        if write_header:
            writer.writeheader()
        writer.writerows(results)

    print(f"Saved to {NARRATIVES_CSV}")


if __name__ == "__main__":
    main()
