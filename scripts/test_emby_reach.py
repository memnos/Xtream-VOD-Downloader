import requests

urls = [
    "http://127.0.0.1:8096/emby/System/Info/Public",
    "http://172.17.0.1:8096/emby/System/Info/Public",
    "http://host.docker.internal:8096/emby/System/Info/Public",
]
for url in urls:
    try:
        r = requests.get(url, timeout=3)
        print(url, r.status_code)
    except Exception as exc:
        print(url, "FAIL", exc)
