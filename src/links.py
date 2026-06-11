"""Deep links into Google Flights — the exact one-way search (airports, date,
party size, cabin) behind each price on the dashboard."""


def gf_link(origin, dest, dep_date, adults, children, max_stops=None):
    # A zero-valued stops filter breaks the Google results page ("Oops,
    # something went wrong"); clamp to "1 stop or fewer", which surfaces the
    # nonstops at the top and keeps the link usable.
    if max_stops is not None:
        max_stops = max(1, max_stops)
    try:
        from fast_flights import FlightData, Passengers
        from fast_flights.filter import TFSData
        tfs = TFSData.from_interface(
            flight_data=[FlightData(date=dep_date, from_airport=origin,
                                    to_airport=dest, max_stops=max_stops)],
            trip="one-way",
            passengers=Passengers(adults=adults, children=children,
                                  infants_in_seat=0, infants_on_lap=0),
            seat="economy",
        ).as_b64().decode()
    except Exception:  # noqa: BLE001 — links are decoration, never fail a sweep
        return None
    return f"https://www.google.com/travel/flights?tfs={tfs}&hl=en&curr=USD"
