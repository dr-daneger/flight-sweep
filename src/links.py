"""Deep links into Google Flights — the exact one-way search (airports, date,
party size, cabin) behind each price on the dashboard."""


def gf_link(origin, dest, dep_date, adults, children):
    try:
        from fast_flights import FlightData, Passengers
        from fast_flights.filter import TFSData
        tfs = TFSData.from_interface(
            flight_data=[FlightData(date=dep_date, from_airport=origin,
                                    to_airport=dest)],
            trip="one-way",
            passengers=Passengers(adults=adults, children=children,
                                  infants_in_seat=0, infants_on_lap=0),
            seat="economy",
        ).as_b64().decode()
    except Exception:  # noqa: BLE001 — links are decoration, never fail a sweep
        return None
    return f"https://www.google.com/travel/flights?tfs={tfs}&hl=en&curr=USD"
