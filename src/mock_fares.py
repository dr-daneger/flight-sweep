"""Deterministic-but-plausible fares so the pipeline can run without API keys.

Prices are party totals (5 seats) seeded by route, drifting day to day so the
history sparklines and drop alerts are exercised too.
"""
import datetime
import hashlib
import math
import random

CARRIERS = {
    ("PDX", "DFW"): ["AA", "AS", "WN"],
    ("DFW",): ["AA", "BA", "AF", "LH", "IB"],
    ("BOD",): ["AF", "KL", "BA", "LH"],
}

# rough per-person one-way base fares (USD) for mid-October
BASE_PP = {
    "PDX-DFW": 165,
    "DFW-LIS": 640, "DFW-OPO": 690, "DFW-MAD": 580, "DFW-BCN": 560,
    "DFW-SVQ": 720, "DFW-NCE": 680, "DFW-MRS": 700, "DFW-FCO": 610,
    "DFW-MXP": 590, "DFW-VCE": 650, "DFW-GVA": 670, "DFW-CDG": 540,
    "BOD-PDX": 620,
}


def _seeded(route, dep_date, day):
    h = hashlib.md5(f"{route}|{dep_date}|{day}".encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def search(origin, dest, dep_date, adults, children, currency="USD",
           max_offers=5, window=None, **_):
    from .timewin import filter_offers
    route = f"{origin}-{dest}"
    base = BASE_PP.get(route, 650)
    seats = adults + children
    day = datetime.date.today().toordinal()
    rng = _seeded(route, dep_date, day)
    # slow sinusoidal market drift + daily jitter
    drift = 1 + 0.12 * math.sin(day / 9 + int(hashlib.md5(route.encode()).hexdigest()[:4], 16))
    carriers = CARRIERS.get((origin, dest)) or CARRIERS.get((origin,), ["XX"])
    jitter = random.uniform(-0.05, 0.05)  # unseeded: consecutive sweeps differ
    offers = []
    for i in range(max_offers):
        pp = base * drift * (1 + jitter) * (1 + 0.06 * i + rng.uniform(-0.04, 0.04))
        dep_hour = rng.choice([7, 9, 11, 13, 16, 18, 20])
        dur = rng.randint(240, 280) if route == "PDX-DFW" else rng.randint(680, 980)
        arr = (datetime.datetime.fromisoformat(f"{dep_date}T{dep_hour:02d}:10")
               + datetime.timedelta(minutes=dur))
        offers.append({
            "price": round(pp * seats, 2),
            "currency": currency,
            "carrier": carriers[i % len(carriers)],
            "stops": 0 if route == "PDX-DFW" and i == 0 else rng.choice([1, 1, 2]),
            "duration_min": dur,
            "dep_time": f"{dep_date}T{dep_hour:02d}:10",
            "arr_time": arr.isoformat(timespec="minutes"),
        })
    offers = filter_offers(offers, window)
    offers.sort(key=lambda o: o["price"])
    return offers
