#!/usr/bin/env python3
"""Fix Serie Tv library metadata fetchers (empty TypeOptions = no TMDB/TVDB lookup)."""
import json
import urllib.parse
import urllib.request

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
HEADERS = {"X-Emby-Token": API_KEY, "Content-Type": "application/json"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def series_type_options():
  """Default Emby TV metadata fetchers (mirror Film library style)."""
  fetchers = ["TheMovieDb", "TheTVDB"]
  image_fetchers = ["TheMovieDb", "TheTVDB", "FanArt"]
  base = {
      "MetadataFetchers": fetchers,
      "MetadataFetcherOrder": fetchers + ["The Open Movie Database"],
      "ImageFetchers": image_fetchers + ["Image Capture"],
      "ImageFetcherOrder": image_fetchers + ["The Open Movie Database", "Image Capture"],
      "ImageOptions": [],
  }
  return [
      {**base, "Type": "Series"},
      {**base, "Type": "Season"},
      {**base, "Type": "Episode"},
  ]


def main():
    libs = get("/emby/Library/VirtualFolders")
    lib = next((l for l in libs if l["Name"] == "Serie Tv"), None)
    if not lib:
        print("Libreria 'Serie Tv' non trovata")
        return 1

    lib_id = lib["ItemId"]
    opts = dict(lib.get("LibraryOptions") or {})

    print("PRIMA:")
    print("  TypeOptions:", len(opts.get("TypeOptions") or []))
    print("  PreferredMetadataLanguage:", repr(opts.get("PreferredMetadataLanguage")))
    print("  MetadataSavers:", opts.get("MetadataSavers"))

    opts["TypeOptions"] = series_type_options()
    opts["PreferredMetadataLanguage"] = "it"
    opts["PreferredImageLanguage"] = "it"
    opts["MetadataCountryCode"] = "IT"
    opts["SaveLocalMetadata"] = True
    opts["MetadataSavers"] = ["Nfo"]
    opts["LocalMetadataReaderOrder"] = ["Nfo"]
    opts["PlaceholderMetadataRefreshIntervalDays"] = 7

    post("/emby/Library/VirtualFolders/LibraryOptions", {"Id": lib_id, "LibraryOptions": opts})
    print("\nConfigurazione aggiornata.")

    # refresh metadata on library root
    post(
        f"/emby/Items/{lib_id}/Refresh?"
        + urllib.parse.urlencode({
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "Default",
            "ReplaceAllMetadata": "false",
        }),
        {},
    )
    print("Refresh metadati avviato sulla libreria Serie Tv (in background).")
    print("Attendi qualche minuto e ricontrolla le serie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
