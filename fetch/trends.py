"""Google Trends fetcher for treatment-level trend radar.

Uses pytrends (unofficial Google Trends client — free, no API key).
Pulls 30d interest for the treatments and topics Aesura focuses on,
and flags risers and fallers.
"""

from __future__ import annotations

import time
from typing import Any

from pytrends.request import TrendReq


# Aesura's treatment menu, grouped by category. Each entry maps the Google
# Trends query term to the content-keywords we also match against post titles
# (so we can compute content coverage per treatment). Keep content_keywords
# lowercase — matching is case-insensitive.
TREATMENTS: dict[str, list[dict]] = {
    "Regenerative / longevity": [
        {"term": "exosomes",          "content_keywords": ["exosome"]},
        {"term": "stem cell therapy", "content_keywords": ["stem cell"]},
        {"term": "NAD+ IV",           "content_keywords": ["nad+", "nad iv"]},
        {"term": "longevity clinic",  "content_keywords": ["longevity clinic", "longevity medicine", "longevity doctor"]},
        {"term": "plasmapheresis",    "content_keywords": ["plasmapheresis"]},
    ],
    "Metabolic / peptide": [
        {"term": "peptide therapy",   "content_keywords": ["peptide", "p3ptide"]},
        {"term": "semaglutide",       "content_keywords": ["semaglutide", "ozempic"]},
        {"term": "tirzepatide",       "content_keywords": ["tirzepatide", "mounjaro", "zepbound"]},
        {"term": "retatrutide",       "content_keywords": ["retatrutide"]},
        {"term": "HRT",               "content_keywords": ["hrt", "hormone replacement"]},
    ],
    "Aesthetic / skin": [
        {"term": "Sculptra",          "content_keywords": ["sculptra"]},
        {"term": "Rejuran",           "content_keywords": ["rejuran", "pdrn"]},
        {"term": "Fraxel",            "content_keywords": ["fraxel"]},
        {"term": "Botox",             "content_keywords": ["botox", "neurotoxin"]},
        {"term": "dermal fillers",    "content_keywords": ["filler", "juvederm", "restylane"]},
    ],
    "Performance / recovery": [
        {"term": "red light therapy", "content_keywords": ["red light"]},
        {"term": "hyperbaric oxygen", "content_keywords": ["hyperbaric", "hbot"]},
        {"term": "ketamine therapy",  "content_keywords": ["ketamine"]},
        {"term": "PRP injection",     "content_keywords": ["prp"]},
        {"term": "cold plunge",       "content_keywords": ["cold plunge", "cryotherapy"]},
    ],
    "Screening": [
        {"term": "Galleri test",      "content_keywords": ["galleri", "cancer screen"]},
    ],
}


def _flatten_terms() -> list[str]:
    return [t["term"] for bucket in TREATMENTS.values() for t in bucket]


def treatment_category(term: str) -> str | None:
    for cat, bucket in TREATMENTS.items():
        for t in bucket:
            if t["term"] == term:
                return cat
    return None


# Flat term list used by the existing top-level trend-alerts section.
TERMS: list[str] = _flatten_terms()

# Geographic slices. US = content-strategy signal; NY metro (DMA 501) =
# Aesura's service area proxy (covers Hackensack NJ + NYC + CT Fairfield
# + Westchester/Long Island).
GEOS: dict[str, str] = {
    "national": "US",
    "ny_metro": "US-NY-501",
}

TIMEFRAME = "today 3-m"   # last 90 days, daily granularity
COMPARE_WINDOW = 30        # compare latest 30d mean vs previous 30d mean


def _fetch_geo(geo_code: str) -> dict[str, Any]:
    try:
        # retries=0 avoids a bug in pytrends + urllib3>=1.26 where Retry()
        # is called with the removed `method_whitelist` kwarg.
        py = TrendReq(hl="en-US", tz=300, retries=0)
    except Exception as exc:  # noqa: BLE001
        return {"geo": geo_code, "error": f"pytrends init failed: {exc}", "terms": [], "rising": [], "falling": []}

    rows: list[dict] = []
    errors: list[dict] = []

    def _query(term: str):
        """Try once, and on 429 back off 60s and retry — pytrends hammers Google
        fast enough to trip throttling when the term list is 20+ long."""
        for attempt in range(2):
            try:
                py.build_payload([term], timeframe=TIMEFRAME, geo=geo_code)
                return py.interest_over_time(), None
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "429" in msg and attempt == 0:
                    time.sleep(60)
                    continue
                return None, msg
        return None, "retry exhausted"

    for term in TERMS:
        df, err = _query(term)
        if err:
            errors.append({"term": term, "error": err})
            time.sleep(3)
            continue

        if df is None or df.empty or term not in df.columns:
            errors.append({"term": term, "error": "no data"})
            time.sleep(1.5)
            continue

        series = df[term].astype(int).tolist()
        if len(series) < COMPARE_WINDOW * 2:
            time.sleep(0.5)
            continue

        recent = series[-COMPARE_WINDOW:]
        prior = series[-COMPARE_WINDOW * 2 : -COMPARE_WINDOW]
        recent_avg = sum(recent) / len(recent)
        prior_avg = max(sum(prior) / len(prior), 0.1)
        delta_pct = round((recent_avg - prior_avg) / prior_avg * 100, 1)
        rows.append(
            {
                "term": term,
                "recent_avg": round(recent_avg, 1),
                "prior_avg": round(prior_avg, 1),
                "delta_pct": delta_pct,
                "series": series[-60:],
            }
        )
        time.sleep(1.5)

    rising = sorted([r for r in rows if r["delta_pct"] > 10], key=lambda r: r["delta_pct"], reverse=True)
    falling = sorted([r for r in rows if r["delta_pct"] < -10], key=lambda r: r["delta_pct"])

    return {
        "geo": geo_code,
        "terms": rows,
        "rising": rising,
        "falling": falling,
        "errors": errors,
    }


def fetch() -> dict[str, Any]:
    return {
        "timeframe": TIMEFRAME,
        "compare_window_days": COMPARE_WINDOW,
        "slices": {name: _fetch_geo(code) for name, code in GEOS.items()},
    }
