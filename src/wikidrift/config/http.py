"""HTTP identity, endpoints, session + the shared retry policy — the stable external-API layer of config."""
import time

import requests

# awesome@rpophesagr.com is the project contact for shared-API User-Agents (not a personal address).
UA = "gh-wiki/0.1 (awesome@rpophesagr.com; wikipedia-drift-detector research)"
WIKIWHO = "https://wikiwho.wmcloud.org/en/api/v1.0.0-beta"
ACTION = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"


def action(lang="en"):
    """Per-language MediaWiki Action API endpoint (ACTION is the en default). Used by L5."""
    return f"https://{lang}.wikipedia.org/w/api.php"


def session():
    """A requests.Session pre-set with the project User-Agent (polite to shared APIs)."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def get_json_retrying(sess, url, params=None, timeout=25, attempts=4):
    """GET `url` → parsed JSON, retrying transient network/API errors with linear backoff.
    Raises the last exception after `attempts`. One home for the retry policy every Action/Wikidata
    adapter (provenance/ingest/l4/l5_sources) shares — tune it once."""
    for attempt in range(attempts):
        try:
            return sess.get(url, params=params, timeout=timeout).json()
        except Exception:                               # noqa: BLE001 — network/decode; retry then re-raise
            if attempt == attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
