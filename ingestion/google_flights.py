"""Fetch a Google Flights search (Best + Cheapest tabs) and merge results per flight."""

import base64
import time

from primp import Client

from .config import DELAY_SECONDS
from .parser import extract_payload, parse_flights, parse_price_insights

URL = "https://www.google.com/travel/flights/search"
# tfu selects which results tab Google renders into the HTML (field 4: 1 = Best, 2 = Cheapest)
TABS = {"listed": "EgYIACABKBIiAA", "cheapest": "EgYIACACKBMiAA"}

_client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1  # negative ints are encoded as 64-bit two's complement
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _num(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _msg(field: int, payload: bytes | str) -> bytes:
    data = payload.encode() if isinstance(payload, str) else payload
    return _varint(field << 3 | 2) + _varint(len(data)) + data


def build_tfs(origin: str, dest: str, flight_date: str) -> str:
    """One-way, 1 adult, economy — byte-for-byte what the Google Flights website sends.

    fast-flights' tfs omits fields 1, 2, 14, 16 and the airport type flag; without them the
    Cheapest tab missed booking-site fares (IXC-DEL 2026-10-10: 5638 vs browser 5510).
    """
    leg = (
        _msg(2, flight_date)
        + _msg(13, _num(1, 1) + _msg(2, origin))
        + _msg(14, _num(1, 1) + _msg(2, dest))
    )
    data = (
        _num(1, 28) + _num(2, 2) + _msg(3, leg)
        + _num(8, 1)             # passenger: adult
        + _num(9, 1)             # seat: economy
        + _num(14, 1)
        + _msg(16, _num(1, -1))  # no max price
        + _num(19, 2)            # trip: one-way
    )
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def fetch_tab(origin: str, dest: str, flight_date: str, tab: str) -> list:
    res = _client.get(URL, params={
        "tfs": build_tfs(origin, dest, flight_date), "tfu": TABS[tab], "hl": "en", "curr": "INR",
    })
    if res.status_code != 200:
        raise RuntimeError(f"HTTP {res.status_code}")
    return extract_payload(res.text)


def merge(listed: list[dict], cheapest: list[dict]) -> list[dict]:
    """One row per itinerary with price_listed_inr / price_cheapest_inr (None if absent or unpriced)."""
    merged: dict[tuple, dict] = {}
    for tab, flights in (("listed", listed), ("cheapest", cheapest)):
        for f in flights:
            key = (tuple(f["flight_numbers"]), f["dep_time"])
            row = merged.setdefault(key, {
                **{k: v for k, v in f.items() if k not in ("price_inr", "section")},
                "price_listed_inr": None,
                "price_cheapest_inr": None,
                "listed_section": None,
            })
            if f["price_inr"] is not None:
                row[f"price_{tab}_inr"] = f["price_inr"]
            if tab == "listed":
                row["listed_section"] = f["section"]
    return list(merged.values())


def scrape(origin: str, dest: str, flight_date: str) -> dict:
    """Fetch both tabs. Raises if both fail; a single failed tab is reported in tab_errors."""
    payloads, errors = {}, {}
    for i, tab in enumerate(TABS):
        if i:
            time.sleep(DELAY_SECONDS)
        try:
            payloads[tab] = fetch_tab(origin, dest, flight_date, tab)
        except Exception as e:
            errors[tab] = f"{type(e).__name__}: {str(e)[:300]}"
    if not payloads:
        raise RuntimeError(f"both tabs failed: {errors}")

    flights = merge(
        parse_flights(payloads["listed"]) if "listed" in payloads else [],
        parse_flights(payloads["cheapest"]) if "cheapest" in payloads else [],
    )
    # Price history is only present on the Best-tab page
    insights = parse_price_insights(payloads.get("listed") or payloads["cheapest"])
    return {"flights": flights, "price_insights": insights, "tab_errors": errors}
