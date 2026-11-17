#!/usr/bin/env python3
"""Move a "now" marker on a Miro board down a static agenda.

The marker's y position is interpolated from AGENDA_STOPS based on the current
time in Europe/Berlin. x is left untouched.

Environment:
  MIRO_ACCESS_TOKEN  (secret) Miro REST API token with boards:write
  MIRO_BOARD_ID      (secret) e.g. uXjVxxxxxxx=
  MIRO_ITEM_ID       (secret) id of the marker item (a thin rectangle shape)
  AGENDA_STOPS       "HH:MM=y,HH:MM=y,..." e.g. "09:00=-500,17:00=1400"
                     At least two stops. Linear interpolation between them.
  OFFSITE_DATES      optional, "YYYY-MM-DD,YYYY-MM-DD". If set, do nothing on other days.
  INTERVAL_SECONDS   optional, default 120 (only used with --loop)
  MAX_RUNTIME_MIN    optional, default 350 (stay under GitHub's 6h job limit)
  TZ_NAME            optional, default Europe/Berlin

Usage:
  move_obj.py            move once
  move_obj.py --loop     move every INTERVAL_SECONDS until the last stop or MAX_RUNTIME_MIN
  move_obj.py --inspect  print the item's current position (use this to calibrate AGENDA_STOPS)
  move_obj.py --at 10:30 move once as if it were 10:30 (testing; ignores OFFSITE_DATES)

"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://api.miro.com/v2"


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"Missing environment variable: {name}")
    return value


TOKEN = env("MIRO_ACCESS_TOKEN")
BOARD_ID = env("MIRO_BOARD_ID").strip().strip("/")  # tolerate a "/" copied from the board URL
ITEM_ID = env("MIRO_ITEM_ID")
TZ = ZoneInfo(os.environ.get("TZ_NAME") or "Europe/Berlin")


def miro(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}/boards/{urllib.parse.quote(BOARD_ID, safe='')}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Miro {method} {path} -> {e.code}: {e.read().decode(errors='replace')}") from e


def parse_stops(raw):
    stops = []
    for part in raw.split(","):
        hhmm, y = part.strip().split("=")
        h, m = hhmm.split(":")
        stops.append((int(h) * 60 + int(m), float(y)))
    stops.sort()
    if len(stops) < 2:
        sys.exit("AGENDA_STOPS needs at least two stops")
    return stops


def target_y(now, stops):
    minute = now.hour * 60 + now.minute + now.second / 60
    if minute <= stops[0][0]:
        return stops[0][1]
    if minute >= stops[-1][0]:
        return stops[-1][1]
    for (t0, y0), (t1, y1) in zip(stops, stops[1:]):
        if t0 <= minute <= t1:
            return y0 + (y1 - y0) * (minute - t0) / (t1 - t0)


def get_position():
    item = miro("GET", f"/items/{ITEM_ID}")
    return item["position"], item.get("parent")


def move_to(x, y, parent):
    body = {"position": {"x": x, "y": round(y, 1)}}
    if parent:  # position is relative to the parent frame; keep it there
        body["parent"] = {"id": parent["id"]}
    miro("PATCH", f"/items/{ITEM_ID}", body)


def main():
    if "--inspect" in sys.argv:
        pos, parent = get_position()
        print(json.dumps({"position": pos, "parent": parent}, indent=2))
        return

    # --at HH:MM: move once as if it were that time (for testing; skips OFFSITE_DATES)
    if "--at" in sys.argv:
        h, m = sys.argv[sys.argv.index("--at") + 1].split(":")
        at = datetime.now(TZ).replace(hour=int(h), minute=int(m), second=0)
        stops = parse_stops(env("AGENDA_STOPS"))
        pos, parent = get_position()
        y = target_y(at, stops)
        move_to(pos["x"], y, parent)
        print(f"moved to y={y:.1f} (as if {at:%H:%M}), was y={pos['y']}")
        return

    now = datetime.now(TZ)
    dates = [d.strip() for d in os.environ.get("OFFSITE_DATES", "").split(",") if d.strip()]
    if dates and now.date().isoformat() not in dates:
        print(f"{now.date()} is not an offsite date, nothing to do")
        return

    stops = parse_stops(env("AGENDA_STOPS"))
    loop = "--loop" in sys.argv
    interval = int(os.environ.get("INTERVAL_SECONDS") or 120)
    deadline = now + timedelta(minutes=int(os.environ.get("MAX_RUNTIME_MIN") or 350))
    end_of_day = now.replace(hour=stops[-1][0] // 60, minute=stops[-1][0] % 60, second=0, microsecond=0)

    pos, parent = get_position()
    x, last_y = pos["x"], None
    while True:
        now = datetime.now(TZ)
        y = target_y(now, stops)
        if last_y is None or abs(y - last_y) >= 0.5:
            try:
                move_to(x, y, parent)
                print(f"{now:%H:%M:%S} y={y:.1f}", flush=True)
                last_y = y
            except (RuntimeError, OSError) as e:
                if not loop:
                    raise
                print(f"{now:%H:%M:%S} update failed, retrying next tick: {e}", flush=True)
        if not loop or now >= end_of_day or now >= deadline:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
