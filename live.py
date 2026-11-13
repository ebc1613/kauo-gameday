"""Live game-day snapshot for KAUO (runs in GitHub Actions every ~10 minutes on home-game weekends).

Reads the public adsb.lol feed for aircraft within 50 NM of the field and writes live.json for the page.
Only aggregate numbers and aircraft types are published: no tail numbers, callsigns or ICAO codes.
Flight-school aircraft (AUB callsigns, AUxx tails) are left out so the numbers show visitors.

Python 3 standard library only. Usage: python3 live.py [--force]
"""
import hashlib
import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LAT, LON, ELEV = 32.6151, -85.4340, 777
TZ = ZoneInfo("America/Chicago")
OUT = Path(__file__).with_name("live.json")
DAYS = Path(__file__).with_name("live_days.json")   # written nightly by the pipeline: dates to run on
URL = f"https://api.adsb.lol/v2/point/{LAT}/{LON}/50"


def nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def school(a):
    return (a.get("flight") or "").startswith("AUB") or ((a.get("r") or "").endswith("AU") and (a.get("r") or "").startswith("N"))


def main():
    now = datetime.now(timezone.utc)
    local = now.astimezone(TZ)
    force = "--force" in sys.argv
    try:
        days = set(json.loads(DAYS.read_text()))
    except (OSError, ValueError):
        days = set()
    if not force and (local.date().isoformat() not in days or not 6 <= local.hour < 23):
        print("not a live window; nothing to do")
        return
    req = urllib.request.Request(URL, headers={"User-Agent": "kauo-gameday-live (github actions)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        ac = json.load(r).get("ac", [])

    try:
        prev = json.loads(OUT.read_text())
    except (OSError, ValueError):
        prev = {}
    today = local.date().isoformat()
    state = prev.get("state", {}) if prev.get("date") == today else {}
    history = prev.get("history", []) if prev.get("date") == today else []
    landed = [x for x in prev.get("landed", []) if prev.get("date") == today]

    salt = today  # anonymous ids that change daily, only used to follow aircraft between snapshots
    inbound, near, total, seen_now = [], 0, 0, {}
    for a in ac:
        if a.get("lat") is None or school(a):
            continue
        total += 1
        d = nm(a["lat"], a["lon"], LAT, LON)
        alt = a.get("alt_baro")
        gnd = alt == "ground"
        agl = None if gnd or not isinstance(alt, (int, float)) else alt - ELEV
        aid = hashlib.sha1(f"{salt}{a.get('hex')}".encode()).hexdigest()[:10]
        seen_now[aid] = {"d": round(d, 1), "agl": agl, "gnd": gnd}
        if d <= 10 and (gnd or (agl is not None and agl <= 3000)):
            near += 1
        if gnd or agl is None or agl > 18000:
            continue
        off = abs((bearing(a["lat"], a["lon"], LAT, LON) - (a.get("track") or 0) + 180) % 360 - 180)
        rate = a.get("baro_rate") or a.get("geom_rate") or 0
        if off <= 30 and d > 2 and (rate <= -300 or (d <= 15 and agl <= 4000)):
            gs = a.get("gs") or 0
            inbound.append({"type": a.get("t"), "dist_nm": round(d), "alt_ft": int(alt) if isinstance(alt, (int, float)) else None,
                            "gs_kt": round(gs) if gs else None, "eta_min": round(d / gs * 60) if gs > 40 else None})

    # "likely landed": last seen low and close to the field, now on the ground there or gone
    cutoff = (now - timedelta(hours=1)).isoformat()
    for aid, s in state.items():
        was_low = s["d"] <= 4 and s["agl"] is not None and s["agl"] <= 1500
        cur = seen_now.get(aid)
        if was_low and (cur is None or (cur["gnd"] and cur["d"] <= 2)) and aid not in {x["id"] for x in landed}:
            landed.append({"id": aid, "t": now.isoformat()})
    landed = [x for x in landed if x["t"] >= cutoff or x["t"][:10] == today][-200:]
    history.append({"t": local.strftime("%H:%M"), "near": near})

    out = {
        "updated": now.isoformat(), "date": today,
        "inbound_count": len(inbound), "near_count": near, "total_50nm": total,
        "landed_last_hour": sum(1 for x in landed if x["t"] >= cutoff),
        "inbound": sorted(inbound, key=lambda x: x["dist_nm"]),
        "history": history[-120:], "landed": landed,
        "state": {k: v for k, v in seen_now.items() if v["d"] <= 12},
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"{local:%H:%M} inbound {len(inbound)} near {near} total {total} landed/h {out['landed_last_hour']}")


if __name__ == "__main__":
    main()
