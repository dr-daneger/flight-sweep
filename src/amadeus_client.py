"""Thin Amadeus Self-Service Flight Offers Search client.

Needs AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET in the environment.
Free tier: register at https://developers.amadeus.com (Self-Service, no card).
"""
import os
import re
import time
import requests

from .timewin import filter_offers


class AmadeusClient:
    def __init__(self, hostname="https://test.api.amadeus.com"):
        self.hostname = hostname.rstrip("/")
        self.client_id = os.environ.get("AMADEUS_CLIENT_ID")
        self.client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")
        self._token = None
        self._token_expiry = 0
        if not (self.client_id and self.client_secret):
            raise RuntimeError(
                "Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET "
                "(free keys: https://developers.amadeus.com)")

    def _auth(self):
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = requests.post(
            f"{self.hostname}/v1/security/oauth2/token",
            data={"grant_type": "client_credentials",
                  "client_id": self.client_id,
                  "client_secret": self.client_secret},
            timeout=30)
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + body.get("expires_in", 1700)
        return self._token

    def search(self, origin, dest, dep_date, adults, children, cabin="ECONOMY",
               currency="USD", max_offers=5, window=None, max_stops=None, **_):
        """Return up to max_offers one-way offers, cheapest first.

        price = grand total for the whole party. Returns [] on API errors so a
        single bad route never kills the sweep.
        """
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": dep_date,
            "adults": adults,
            "currencyCode": currency,
            "travelClass": cabin,
            "max": max_offers * 4,  # over-fetch, then dedupe by carrier
        }
        if children:
            params["children"] = children
        if max_stops == 0:
            # the API supports nonstop natively; looser limits are filtered
            # post-parse below (segments are always known here)
            params["nonStop"] = "true"
        try:
            resp = requests.get(
                f"{self.hostname}/v2/shopping/flight-offers",
                params=params,
                headers={"Authorization": f"Bearer {self._auth()}"},
                timeout=60)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except requests.RequestException as exc:
            print(f"  ! amadeus {origin}->{dest} {dep_date}: {exc}")
            return []

        offers, seen_carriers = [], set()
        for o in sorted(data, key=lambda x: float(x["price"]["grandTotal"])):
            itin = o["itineraries"][0]
            segs = itin["segments"]
            # window applies before truncation: Amadeus always has dep times
            if not filter_offers([{"dep_time": segs[0]["departure"]["at"]}], window):
                continue
            if max_stops is not None and len(segs) - 1 > max_stops:
                continue
            carrier = (o.get("validatingAirlineCodes") or [segs[0]["carrierCode"]])[0]
            if carrier in seen_carriers and len(data) > max_offers:
                continue
            seen_carriers.add(carrier)
            offers.append({
                "price": float(o["price"]["grandTotal"]),
                "currency": o["price"]["currency"],
                "carrier": carrier,
                "stops": len(segs) - 1,
                "duration_min": _iso_minutes(itin["duration"]),
                "dep_time": segs[0]["departure"]["at"],
                "arr_time": segs[-1]["arrival"]["at"],
            })
            if len(offers) >= max_offers:
                break
        time.sleep(0.5)  # stay well under free-tier rate limits
        return offers


def _iso_minutes(iso):  # "PT11H35M" -> 695
    h = re.search(r"(\d+)H", iso)
    m = re.search(r"(\d+)M", iso)
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
