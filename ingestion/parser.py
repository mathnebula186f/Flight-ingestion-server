"""Parse the flight data embedded in a Google Flights results page.

Payload map (script.ds:1):
  payload[2][0]            best flights  -> list of items
  payload[3][0]            other flights -> list of items
  item[0]                  itinerary: [0] airline code, [1] airline names, [2] legs, [9] total minutes
  item[1][0][1]            price (missing = Google shows "price unavailable" -> kept as None)
  leg[3]/[6]               from / to airport code      leg[4]/[5] from / to airport name
  leg[8]/[10]              dep / arr time [h, m]       leg[20]/[21] dep / arr date [y, m, d]
  leg[11]                  duration minutes            leg[17] plane type
  leg[22]                  [airline code, flight number, _, airline name]
  payload[5][1][1]         current lowest price on the route
  payload[5][4][1]/[5][1]  typical price range low / high
  payload[5][10][0]        price history: [[epoch_ms, lowest_price], ...]
"""

import json
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from .config import IST


class GoogleFlightsError(Exception):
    pass


def extract_payload(html: str) -> list:
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise GoogleFlightsError("flight data not found in page (blocked or consent page?)")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise GoogleFlightsError("Google returned an error for this search")
    return json.loads(data)


def _get(obj, *path):
    for key in path:
        try:
            obj = obj[key]
        except (IndexError, KeyError, TypeError):
            return None
    return obj


def _hhmm(value) -> str:
    # Google omits zero parts: [8] = 08:00, [None, 31] = 00:31
    h, m = [*(value or []), None, None][:2]
    return f"{h or 0:02d}:{m or 0:02d}"


def _date(value) -> str | None:
    return f"{value[0]:04d}-{value[1]:02d}-{value[2]:02d}" if value else None


def _parse_leg(leg: list) -> dict:
    number = _get(leg, 22) or []
    return {
        "from": leg[3],
        "from_name": leg[4],
        "to": leg[6],
        "to_name": leg[5],
        "dep_date": _date(leg[20]),
        "dep_time": _hhmm(leg[8]),
        "arr_date": _date(leg[21]),
        "arr_time": _hhmm(leg[10]),
        "duration_mins": leg[11],
        "plane_type": leg[17],
        "airline_code": _get(number, 0),
        "airline_name": _get(number, 3),
        "flight_number": f"{number[0]} {number[1]}" if len(number) > 1 and number[0] and number[1] else None,
    }


def _parse_item(item: list, section: str) -> dict | None:
    itinerary = _get(item, 0)
    if not itinerary or not _get(itinerary, 2):
        return None

    legs = [_parse_leg(leg) for leg in itinerary[2]]
    first, last = legs[0], legs[-1]
    total = itinerary[9] if isinstance(_get(itinerary, 9), int) else sum(l["duration_mins"] or 0 for l in legs)

    return {
        "section": section,
        "flight_numbers": [l["flight_number"] for l in legs],
        "origin": first["from"],
        "destination": last["to"],
        "dep_date": first["dep_date"],
        "dep_time": first["dep_time"],
        "arr_date": last["arr_date"],
        "arr_time": last["arr_time"],
        "duration_mins": total,
        "stops": len(legs) - 1,
        "price_inr": _get(item, 1, 0, 1),
        "legs": legs,
    }


def parse_flights(payload: list) -> list[dict]:
    flights, seen = [], set()
    for section, index in (("best", 2), ("other", 3)):
        for item in _get(payload, index, 0) or []:
            flight = _parse_item(item, section)
            if flight is None:
                continue
            key = (tuple(flight["flight_numbers"]), flight["dep_time"])
            if key not in seen:
                seen.add(key)
                flights.append(flight)
    return flights


def parse_price_insights(payload: list) -> dict | None:
    insights = _get(payload, 5)
    if not insights:
        return None
    history = [
        {"date": datetime.fromtimestamp(ms / 1000, IST).date().isoformat(), "lowest_price_inr": price}
        for ms, price in (_get(insights, 10, 0) or [])
        if price is not None
    ]
    return {
        "lowest_price_inr": _get(insights, 1, 1),
        "typical_low_inr": _get(insights, 4, 1),
        "typical_high_inr": _get(insights, 5, 1),
        "price_history": history,
    }
