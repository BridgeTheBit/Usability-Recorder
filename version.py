"""Installed version and cached GitHub release lookup."""

import json
import time
from urllib.request import Request, urlopen

VERSION = "1.2.7"
_cached = None
_expires = 0.0


def latest_release() -> dict:
    global _cached, _expires
    if _cached is not None and time.monotonic() < _expires:
        return _cached
    result = {"current": VERSION, "latest": None, "update_url": None}
    try:
        request = Request("https://api.github.com/repos/BridgeTheBit/Usability-Recorder/releases/latest",
                          headers={"User-Agent": "Atlas-Update-Check", "Accept": "application/vnd.github+json"})
        with urlopen(request, timeout=5) as response:
            release = json.load(response)
        tag = release.get("tag_name", "").removeprefix("v")
        current = tuple(map(int, VERSION.split(".")))
        remote = tuple(map(int, tag.split(".")))
        result["latest"] = tag
        if remote > current:
            result["update_url"] = release.get("html_url")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    _cached, _expires = result, time.monotonic() + 3600
    return result
