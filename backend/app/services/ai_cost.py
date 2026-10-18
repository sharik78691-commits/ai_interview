"""Per-model AI usage & cost report for the admin panel (pure functions).

Source of truth is the ``activity_log`` ``llm_usage`` rows (one per answer
call): ``provider`` holds the model *key* (e.g. ``groq-gpt-oss``), ``model``
the provider's model id, plus latency, token counts and an error category.

Cost honesty rules (so the owner can trust the numbers):
* Cost = tokens x LIST price per 1M tokens (``PRICING``). Free-tier models still
  show their list-price equivalent ("what it would cost at scale") in
  ``cost_*`` while ``billed_*`` is what you pay today (0 for free tier).
* Streamed live answers usually carry NO provider-reported token counts
  (stream usage is not requested). For those rows tokens are ESTIMATED from the
  model's own average over rows that did report tokens, else a conservative
  fallback — and every total says how much of it was estimated.
* Failed calls (rate limit, timeout...) are counted but cost nothing.
* Prices are editable defaults: override with the ``MODEL_PRICING_JSON`` env var
  (``{"model-key": [input_usd_per_1m, output_usd_per_1m]}``) and
  ``USD_TO_INR``. Verify them against your provider invoices.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.services.business_analytics import EventRow, IST, _percentile, _utc

# (input USD / 1M tokens, output USD / 1M tokens)
PRICING: dict[str, tuple[float, float]] = {
    "groq-gpt-oss": (0.15, 0.60),
    "gemini-flash": (0.10, 0.40),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "qwen-coder": (0.0, 0.0),
    "openrouter-free": (0.0, 0.0),
    "openai-gpt6-luna": (0.25, 2.00),
    "deepseek-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.55, 2.19),
}
# Used only when a model has never reported tokens at all.
FALLBACK_TOKENS = (3000, 700)
DEFAULT_USD_TO_INR = 88.0
WINDOWS: dict[str, int] = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def usd_to_inr() -> float:
    try:
        return float(os.getenv("USD_TO_INR", DEFAULT_USD_TO_INR))
    except ValueError:
        return DEFAULT_USD_TO_INR


def pricing_table() -> dict[str, tuple[float, float]]:
    table = dict(PRICING)
    raw = os.getenv("MODEL_PRICING_JSON", "").strip()
    if raw:
        try:
            for key, pair in json.loads(raw).items():
                table[str(key)] = (float(pair[0]), float(pair[1]))
        except (ValueError, TypeError, IndexError, AttributeError):
            pass  # a malformed override must never break the dashboard
    return table


def _is_ok(e: EventRow) -> bool:
    return e.error_category in (None, "ok")


def _avg_tokens(events: list[EventRow]) -> dict[str, tuple[float, float]]:
    """Per model key: mean (input, output) tokens over rows that reported them."""
    acc: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for e in events:
        if _is_ok(e) and e.input_tokens and e.output_tokens:
            acc[e.provider or "unknown"].append((e.input_tokens, e.output_tokens))
    return {
        k: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v))
        for k, v in acc.items()
    }


def build_cost_report(
    events: list[EventRow],
    catalog: list[dict],
    now: datetime | None = None,
) -> dict:
    now = _utc(now) if now else datetime.now(timezone.utc)
    assert now is not None
    rate = usd_to_inr()
    prices = pricing_table()
    llm = [e for e in events if e.kind == "llm_usage" and e.created_at is not None]
    avg = _avg_tokens(llm)
    meta = {m["key"]: m for m in catalog}

    def tokens_for(e: EventRow) -> tuple[float, float, bool]:
        """(input, output, estimated?) for one successful call."""
        if e.input_tokens and e.output_tokens:
            return float(e.input_tokens), float(e.output_tokens), False
        a = avg.get(e.provider or "unknown", FALLBACK_TOKENS)
        return (float(e.input_tokens or a[0]), float(e.output_tokens or a[1]), True)

    def price(key: str) -> tuple[float, float]:
        return prices.get(key, (0.0, 0.0))

    def cost_usd(key: str, tin: float, tout: float) -> float:
        pin, pout = price(key)
        return (tin * pin + tout * pout) / 1_000_000

    def free(key: str) -> bool:
        m = meta.get(key)
        return bool(m and m.get("freeTier"))

    def empty_row(key: str) -> dict:
        m = meta.get(key, {})
        return {
            "key": key,
            "display_name": m.get("displayName") or key,
            "provider": m.get("provider") or "",
            "model_id": m.get("modelId") or "",
            "free_tier": free(key),
            "calls": 0, "ok": 0, "errors": 0,
            "input_tokens": 0, "output_tokens": 0, "estimated_calls": 0,
            "cost_usd": 0.0, "_lat": [], "_users": set(),
        }

    windows: dict[str, dict] = {}
    for name, days in WINDOWS.items():
        start = now - timedelta(days=days)
        rows: dict[str, dict] = {k: empty_row(k) for k in meta}
        for e in llm:
            ts = _utc(e.created_at)
            if ts is None or ts < start or ts > now:
                continue
            key = e.provider or "unknown"
            r = rows.setdefault(key, empty_row(key))
            r["calls"] += 1
            if e.user_id is not None:
                r["_users"].add(e.user_id)
            if not _is_ok(e):
                r["errors"] += 1
                continue
            r["ok"] += 1
            tin, tout, est = tokens_for(e)
            r["input_tokens"] += int(tin)
            r["output_tokens"] += int(tout)
            r["estimated_calls"] += 1 if est else 0
            r["cost_usd"] += cost_usd(key, tin, tout)
            if e.latency_ms is not None:
                r["_lat"].append(e.latency_ms)

        models = []
        for r in rows.values():
            lat = r.pop("_lat")
            users = r.pop("_users")
            billed = 0.0 if r["free_tier"] else r["cost_usd"]
            models.append({
                **r,
                "cost_usd": round(r["cost_usd"], 6),
                "cost_inr": round(r["cost_usd"] * rate, 4),
                "billed_inr": round(billed * rate, 4),
                "cost_per_call_inr": round(r["cost_usd"] * rate / r["ok"], 4) if r["ok"] else None,
                "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
                "p95_latency_ms": _percentile(lat, 0.95),
                "success_rate_pct": round((r["ok"]) * 100.0 / r["calls"], 1) if r["calls"] else None,
                "unique_users": len(users),
            })
        models.sort(key=lambda m: (m["cost_inr"], m["calls"]), reverse=True)
        tot_calls = sum(m["calls"] for m in models)
        tot_ok = sum(m["ok"] for m in models)
        tot_usd = sum(m["cost_usd"] for m in models)
        est_calls = sum(m["estimated_calls"] for m in models)
        windows[name] = {
            "label": name,
            "days": days,
            "from": start.isoformat(),
            "to": now.isoformat(),
            "totals": {
                "calls": tot_calls,
                "ok": tot_ok,
                "errors": tot_calls - tot_ok,
                "input_tokens": sum(m["input_tokens"] for m in models),
                "output_tokens": sum(m["output_tokens"] for m in models),
                "cost_usd": round(tot_usd, 6),
                "cost_inr": round(tot_usd * rate, 4),
                "billed_inr": round(sum(m["billed_inr"] for m in models), 4),
                "cost_per_call_inr": round(tot_usd * rate / tot_ok, 4) if tot_ok else None,
                "estimated_share_pct": round(est_calls * 100.0 / tot_ok, 1) if tot_ok else None,
                "unique_users": len({
                    e.user_id for e in llm
                    if e.user_id is not None and start <= (_utc(e.created_at) or start) <= now
                }),
            },
            "models": models,
        }

    # ---- time series: hourly for 24h, daily for 7/30/90 -------------------
    def bucket_series(days: int, hourly: bool) -> list[dict]:
        if hourly:
            end = now.astimezone(IST).replace(minute=0, second=0, microsecond=0)
            slots = [end - timedelta(hours=i) for i in range(23, -1, -1)]
            key_of = lambda ts: ts.astimezone(IST).replace(minute=0, second=0, microsecond=0)  # noqa: E731
            label_of = lambda s: s.strftime("%H:00")  # noqa: E731
        else:
            today = now.astimezone(IST).date()
            slots_d = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
            slots = slots_d  # type: ignore[assignment]
            key_of = lambda ts: ts.astimezone(IST).date()  # noqa: E731
            label_of = lambda s: s.strftime("%d %b")  # noqa: E731
        buckets: dict = {s: {"label": label_of(s), "calls": 0, "cost_usd": 0.0, "by_model": {}} for s in slots}
        for e in llm:
            ts = _utc(e.created_at)
            if ts is None:
                continue
            b = buckets.get(key_of(ts))
            if b is None:
                continue
            key = e.provider or "unknown"
            m = b["by_model"].setdefault(key, {"calls": 0, "cost_inr": 0.0})
            m["calls"] += 1
            b["calls"] += 1
            if _is_ok(e):
                tin, tout, _ = tokens_for(e)
                c = cost_usd(key, tin, tout)
                b["cost_usd"] += c
                m["cost_inr"] += c * rate
        out = []
        for b in buckets.values():
            out.append({
                "label": b["label"],
                "calls": b["calls"],
                "cost_inr": round(b["cost_usd"] * rate, 4),
                "by_model": {k: {"calls": v["calls"], "cost_inr": round(v["cost_inr"], 4)}
                             for k, v in b["by_model"].items()},
            })
        return out

    series = {
        "24h": bucket_series(1, hourly=True),
        "7d": bucket_series(7, hourly=False),
        "30d": bucket_series(30, hourly=False),
        "90d": bucket_series(90, hourly=False),
    }

    ok_all = [e for e in llm if _is_ok(e)]
    reported = sum(1 for e in ok_all if e.input_tokens and e.output_tokens)
    d7 = windows["7d"]["totals"]
    run_rate = {
        "monthly_cost_inr": round(d7["cost_inr"] * 30 / 7, 2),
        "monthly_billed_inr": round(d7["billed_inr"] * 30 / 7, 2),
        "based_on": "last 7 days x 30/7",
    }
    return {
        "generated_at": now.isoformat(),
        "usd_to_inr": rate,
        "token_coverage_pct": round(reported * 100.0 / len(ok_all), 1) if ok_all else None,
        "pricing": [
            {
                "key": k,
                "display_name": (meta.get(k) or {}).get("displayName") or k,
                "input_per_m_usd": prices.get(k, (0.0, 0.0))[0],
                "output_per_m_usd": prices.get(k, (0.0, 0.0))[1],
                "free_tier": free(k),
                "known_price": k in prices,
            }
            for k in meta
        ],
        "windows": windows,
        "series": series,
        "run_rate": run_rate,
    }


__all__ = ["build_cost_report", "pricing_table", "usd_to_inr", "WINDOWS"]
