"""Shared paths, HTTP retry and the bronze download cache for the reference-data build.

Implements ING-1 (raw public files land in bronze untouched, each with an ingestion-metadata sidecar:
source URL, fetch time, byte count and SHA-256) for S1-S3.

Downloaded files are treated as untrusted data: they are only ever parsed (CSV/JSON/zip members read as
bytes), never executed or imported.
"""

import datetime as dt
import hashlib
import json
import os
import pathlib
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = pathlib.Path(os.environ.get("EPISCOPE_DATA_DIR", ROOT / "data"))
BRONZE = DATA / "bronze"
REFERENCE = DATA / "reference"
DASHBOARD_DATA = ROOT / "dashboard" / "data"

USER_AGENT = "EpiScope/0.1 (CSE 4345 coursework; non-commercial)"


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def meta_path(path):
    path = pathlib.Path(path)
    return path.with_name(path.name + ".meta.json")


def cached_ok(path, expect=None):
    """True if `path` and its sidecar exist, the checksum still matches, and `expect` keys agree."""
    path, mp = pathlib.Path(path), meta_path(path)
    if not (path.exists() and mp.exists()):
        return False
    try:
        meta = json.loads(mp.read_text())
    except (OSError, ValueError):
        return False
    if meta.get("sha256") != sha256_bytes(path.read_bytes()):
        return False
    return all(meta.get(k) == v for k, v in (expect or {}).items())


def write_with_meta(path, content, **meta):
    """Write raw bytes plus the ING-1 sidecar. Writes to a temp name first so a crash leaves no half file."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(path)
    record = {"ingested_at": utc_now(), "bytes": len(content), "sha256": sha256_bytes(content), **meta}
    meta_path(path).write_text(json.dumps(record, indent=2, sort_keys=True))
    return record


def http_get(url, params=None, tries=6, base_wait=5.0, timeout=180, on_429=None):
    """GET with exponential backoff on network errors and 5xx. `on_429(response)` returns seconds to wait."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            wait = base_wait * 2 ** attempt
            print(f"  network error ({e.__class__.__name__}); retry in {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code == 200:
            return r
        if r.status_code == 429:
            wait = on_429(r) if on_429 else base_wait * 2 ** attempt
            print(f"  HTTP 429: {r.text[:160]!r}; waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            wait = base_wait * 2 ** attempt
            print(f"  HTTP {r.status_code}; retry in {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
    raise RuntimeError(f"giving up on {url} after {tries} attempts")


def download(url, dest, source):
    """Fetch `url` into bronze at `dest` unless a verified copy is already cached. Returns the path."""
    dest = pathlib.Path(dest)
    if cached_ok(dest, {"url": url}):
        print(f"cached  {dest.relative_to(DATA)}", flush=True)
        return dest
    print(f"fetch   {url}", flush=True)
    r = http_get(url)
    write_with_meta(dest, r.content, source=source, url=url, http_status=r.status_code)
    return dest
