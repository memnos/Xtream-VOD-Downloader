#!/usr/bin/env python3
"""Apply manual TMDB matches for stubborn movies."""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
H = {"X-Emby-Token": KEY, "Content-Type": "application/json"}
YEAR_RE = re.compile(r"\((\d{4})\)")
REPORT = Path(__file__).with_name("missing_metadata_report.json")

# (search_name, year, pick_index_or_name_substring)
MANUAL = {
    "Agitando le acqua": [("Agitando Le Acque", 2023, 0), ("Making Waves", 2023, 0)],
    "Apocalis'napoli": [("Apocalis Napoli", 2025, 0), ("Apocalisnapoli", None, 0)],
    "Delitto In Alsazia": [("Disparition inquiétante", 2019, 0)],
    "Estonia Coastal Route": [("Estonia Coastal Route", 2026, 0), ("Estonia Coastal Route", None, 0)],
    "Formula criminale": [("Formula criminale", 2025, 0), ("Formula criminale", None, 0)],
    "Geordie Stories Nathan & Dad": [("Geordie Stories", None, 0), ("Geordie Stories Nathan and Dad", None, 0)],
    "Il delitto di Avetrana": [
        ("Il delitto di Avetrana Tutta la verità", 2018, 0),
        ("Il delitto di Avetrana - Tutta la verità", 2018, 0),
        ("Delitto di Avetrana", None, 0),
    ],
    "Il sole dei cattivi": [("Il sole dei cattivi", 2013, 0), ("Il sole dei cattivi", 2014, 0)],
    "L'arte delle otto armi": [
        ("The art of eight limbs", 2026, 0),
        ("L'arte delle otto armi", 2025, 0),
        ("Art of Eight Limbs", 2024, 0),
    ],
    "Mamma e Figlia California Dream": [("Mère et Fille: California Dream", 2016, 0), ("California Dream", 2016, 0)],
    "Mengele, l'angelo della morte di Auschwitz": [("La scomparsa di Josef Mengele", 2025, 0)],
    "La mia vita con Chucky": [("Living with Chucky", 2022, 0)],
    "Odio dal passato": [("My Husband's Killer Affair", 2024, 0), ("Un assassino in famiglia", 2024, 0)],
    "Ritorno al Breithorn": [("Back to the Breithorn", 2024, 0), ("Ritorno al Breithorn", 2024, 0)],
    "Yurena superstar per sempre": [
        ("Sigo siendo la misma", 2025, 0),
        ("I'm Still a Superstar", 2025, 0),
    ],
}


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=120).read())


def post(path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def uid():
    return get("/emby/Users")[0]["Id"]


def has_metadata(item):
    p = item.get("ProviderIds") or {}
    has_id = any(p.get(k) for k in ("Tmdb", "Tvdb", "Imdb"))
    return has_id and (bool((item.get("Overview") or "").strip()) or bool(item.get("PrimaryImageTag") or item.get("ImageTags")))


def search_movie(name, year):
    return post("/emby/Items/RemoteSearch/Movie", {"UserId": uid()}, {"SearchInfo": {"Name": name, "Year": year, "ProviderIds": {}}}) or []


def pick(results, idx=0):
    if not results:
        return None
    return results[min(idx, len(results) - 1)]


def apply(item_id, result):
    post(f"/emby/Items/RemoteSearch/Apply/{item_id}", {"ReplaceAllImages": "true"}, result)
    post(f"/emby/Items/{item_id}/Refresh", {"Recursive": "true", "MetadataRefreshMode": "FullRefresh", "ImageRefreshMode": "Default"})


def main():
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    ids = data["Film"]["missing_ids"]
    ok = fail = 0
    for item_id in ids:
        item = get(f"/emby/Users/{uid()}/Items/{item_id}", {"Fields": "Name,Path,ProviderIds,Overview,PrimaryImageTag,ImageTags"})
        if has_metadata(item):
            print(f"SKIP già ok: {item['Name']}")
            continue
        name = item["Name"]
        tries = MANUAL.get(name, [(name, None, 0)])
        done = False
        for search_name, year, idx in tries:
            results = search_movie(search_name, year)
            result = pick(results, idx)
            if not result:
                continue
            try:
                apply(item_id, result)
                time.sleep(8)
                item2 = get(f"/emby/Users/{uid()}/Items/{item_id}", {"Fields": "ProviderIds,Overview,PrimaryImageTag,ImageTags,Name"})
                if has_metadata(item2):
                    print(f"OK  {name} -> {result.get('Name')} ({result.get('ProductionYear')}) tmdb={(result.get('ProviderIds') or {}).get('Tmdb')}")
                    ok += 1
                    done = True
                    break
                # accept provider id even if overview still loading
                p = item2.get("ProviderIds") or {}
                if any(p.get(k) for k in ("Tmdb", "Imdb")):
                    print(f"OK* {name} -> {result.get('Name')} (provider ok, refresh in corso)")
                    ok += 1
                    done = True
                    break
            except Exception as exc:
                print(f"ERR {name}: {exc}")
        if not done:
            print(f"FAIL {name}")
            fail += 1
        time.sleep(0.5)
    print(f"\nRisultato: OK={ok} FAIL={fail}")


if __name__ == "__main__":
    main()
