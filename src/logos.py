"""Airline logos for the dashboard, embedded as data URIs.

The dashboard must stay self-contained (opens via file://, no CDN), so logos
are inlined rather than hotlinked. Resolution order per carrier:

1. assets/airlines/{IATA}.png committed cache (CI never needs the network for
   carriers we've already seen),
2. one-time fetch from gstatic (the Google Flights logo set), cached for next
   time,
3. generated SVG roundel with the IATA code on a brand-color gradient, so an
   unknown carrier still gets a plausible mark instead of a broken image.
"""
import base64
import hashlib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "assets" / "airlines"
GSTATIC = "https://www.gstatic.com/flights/airline_logos/70px/{code}.png"

# Display name (lowercased) -> IATA code, for every carrier the sources emit.
# gstatic is keyed by IATA code, and codes also drive the roundel fallback.
IATA = {
    "aer lingus": "EI",
    "air canada": "AC",
    "air europa": "UX",
    "air france": "AF",
    "alaska": "AS",
    "american": "AA",
    "austrian": "OS",
    "british airways": "BA",
    "brussels airlines": "SN",
    "condor": "DE",
    "delta": "DL",
    "easyjet": "U2",
    "finnair": "AY",
    "french bee": "BF",
    "frontier": "F9",
    "iberia": "IB",
    "icelandair": "FI",
    "ita airways": "AZ",
    "jetblue": "B6",
    "klm": "KL",
    "lot": "LO",
    "lufthansa": "LH",
    "norse atlantic": "N0",
    "play": "OG",
    "ryanair": "FR",
    "sas": "SK",
    "southwest": "WN",
    "spirit": "NK",
    "swiss": "LX",
    "tap air portugal": "TP",
    "tap portugal": "TP",
    "turkish airlines": "TK",
    "united": "UA",
    "virgin atlantic": "VS",
    "vueling": "VY",
    "westjet": "WS",
}

# Primary brand colors for the SVG roundel fallback.
BRAND = {
    "AA": "#B61F23", "AC": "#D22630", "AF": "#002157", "AS": "#01426A",
    "BA": "#075AAA", "DE": "#FFAD00", "DL": "#C8102E", "EI": "#00A65A",
    "F9": "#046A38", "IB": "#D7192D", "LX": "#E30613", "UA": "#0033A0",
    "UX": "#003893", "B6": "#003876", "LH": "#05164D", "KL": "#00A1DE",
    "TP": "#00A54F", "FI": "#003D7C", "VS": "#E10A0A", "TK": "#C90119",
}


def split_carriers(carrier_str):
    """'American, Iberia' -> ['American', 'Iberia'] (codeshare strings)."""
    return [c.strip() for c in (carrier_str or "").split(",") if c.strip()]


def _to_code(name):
    n = name.strip().lower()
    if n in IATA:
        return IATA[n]
    # Sources are inconsistent about suffixes ("American" vs "American
    # Airlines"); normalize before giving up on a real logo.
    stripped = " ".join(w for w in n.split() if w not in ("airlines", "airways", "air lines"))
    if stripped in IATA:
        return IATA[stripped]
    if len(name.strip()) == 2 and name.strip().isupper():
        return name.strip()  # already an IATA code
    return None


def _roundel(code, name):
    """SVG fallback: IATA code (or initials) on a brand-color gradient disc."""
    label = code or "".join(w[0] for w in name.split()[:2]).upper() or "?"
    color = BRAND.get(code)
    if not color:
        hue = int(hashlib.md5(label.encode()).hexdigest(), 16) % 360
        color = f"hsl({hue},60%,42%)"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="70" height="70">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{color}"/>'
        '<stop offset="1" stop-color="#0c1116"/></linearGradient></defs>'
        '<circle cx="35" cy="35" r="33" fill="url(#g)" '
        'stroke="rgba(255,255,255,.28)" stroke-width="2"/>'
        f'<text x="35" y="44" text-anchor="middle" font-family="Segoe UI,Arial" '
        f'font-size="{26 if len(label) <= 2 else 20}" font-weight="700" '
        f'fill="#fff">{label}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _fetch_png(code):
    """Cache-first gstatic fetch. Returns PNG bytes or None (never raises)."""
    cached = CACHE_DIR / f"{code}.png"
    if cached.exists():
        return cached.read_bytes()
    try:
        req = urllib.request.Request(GSTATIC.format(code=code),
                                     headers={"User-Agent": "flight-sweep/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        if resp.headers.get("Content-Type", "").startswith("image/png") and data:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            return data
    except Exception:
        pass
    return None


def logo_data_uri(name):
    """Best-available logo for one carrier display name, as a data URI."""
    code = _to_code(name)
    if code:
        png = _fetch_png(code)
        if png:
            return "data:image/png;base64," + base64.b64encode(png).decode()
    return _roundel(code, name)


def build_logo_map(payload):
    """Map every individual carrier name in the payload to a logo data URI."""
    names = set()
    for it in payload.get("itineraries", []):
        for leg in ("outbound", "transatlantic", "home"):
            names.update(split_carriers(it.get(leg, {}).get("carrier")))
    for rows in payload.get("legs", {}).values():
        for r in rows:
            names.update(split_carriers(r.get("carrier")))
    return {n: logo_data_uri(n) for n in sorted(names)}
