"""Floorcast — Stage 2: Capacity Engine (Erlang C)."""
import math

def erlang_c_prob_wait(a: float, n: int) -> float:
    """P(wait) for offered load a erlangs, n agents."""
    if n <= a:
        return 1.0
    s = sum(a**k / math.factorial(k) for k in range(n))
    top = a**n / math.factorial(n) * (n / (n - a))
    return top / (s + top)

def service_level(a: float, n: int, aht: float, t: float) -> float:
    if n <= a:
        return 0.0
    pw = erlang_c_prob_wait(a, n)
    return 1 - pw * math.exp(-(n - a) * t / aht)

def agents_required(volume_per_interval: float, aht_sec: float,
                    interval_sec: int = 1800, sl_target: float = 0.8,
                    sl_seconds: float = 20) -> int:
    """Min agents on-seat to hit SL for one interval."""
    if volume_per_interval <= 0:
        return 0
    a = volume_per_interval * aht_sec / interval_sec   # offered erlangs
    n = max(1, math.ceil(a))
    while service_level(a, n, aht_sec, sl_seconds) < sl_target:
        n += 1
        if n > 5000:
            break
    return n

def interval_requirements(daily_volume: float, intraday_profile: list,
                          aht_sec: float, **kw) -> list:
    """intraday_profile: 48 shares summing to 1. Returns 48 agent requirements."""
    return [agents_required(daily_volume * p, aht_sec, **kw) for p in intraday_profile]

def scheduled_headcount(on_seat: int, shrinkage: float = 0.30) -> int:
    """Gross up on-seat requirement for shrinkage (leave, breaks, training)."""
    return math.ceil(on_seat / (1 - shrinkage))
