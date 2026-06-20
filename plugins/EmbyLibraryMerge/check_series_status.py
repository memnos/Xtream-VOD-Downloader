import json
import urllib.parse
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
H = {"X-Emby-Token": KEY}
uid = json.loads(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8096/emby/Users", headers=H)).read())[0]["Id"]
for n in ["Cucina al mercato con Ruben", "Hell's Kitchen USA", "Taratata", "Willy Coyote e Road Runner", "Zelig 30"]:
    url = "http://127.0.0.1:8096/emby/Users/" + uid + "/Items?" + urllib.parse.urlencode(
        {
            "SearchTerm": n,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": "Name,ProviderIds,Overview,PrimaryImageTag,ProductionYear,Path",
            "Limit": "5",
        }
    )
    for i in json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H)).read()).get("Items", []):
        if (i.get("Path") or "").startswith("/data/tv"):
            p = i.get("ProviderIds") or {}
            print(
                f"{n} -> {i['Name']} | tmdb={p.get('Tmdb')} | year={i.get('ProductionYear')} | "
                f"overview={bool(i.get('Overview'))} | poster={bool(i.get('PrimaryImageTag'))}"
            )
