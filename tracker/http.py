"""Shared HTTP session with retries and sane timeouts."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=64, pool_maxsize=64)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


SESSION = _session()


class NotFound(Exception):
    """The board/company does not exist (HTTP 404/410)."""


def _check(r):
    if r.status_code in (404, 410):
        raise NotFound(r.url)
    r.raise_for_status()


def get_json(url, timeout=25, **kw):
    r = SESSION.get(url, timeout=timeout, **kw)
    _check(r)
    return r.json()


def get_text(url, timeout=60, **kw):
    r = SESSION.get(url, timeout=timeout, **kw)
    _check(r)
    return r.text


def post_json(url, payload, timeout=25, **kw):
    r = SESSION.post(url, json=payload, timeout=timeout,
                     headers={"Content-Type": "application/json"}, **kw)
    _check(r)
    return r.json()
