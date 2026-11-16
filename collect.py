"""Sampler: un campione grezzo dell'app in primo piano. Solo stdlib."""
import os
import re
import sqlite3
from urllib.parse import urlparse

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
IDLE_SECS = int(os.environ.get("USAGE_IDLE_SECS", "180"))
WEIGHT_SECS = 5

BROWSER_URL_SCRIPT = {
    "Dia": 'tell application "Dia" to get URL of active tab of front window',
    "Google Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
    "Chromium": 'tell application "Chromium" to get URL of active tab of front window',
    "Arc": 'tell application "Arc" to get URL of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to get URL of active tab of front window',
    "Safari": 'tell application "Safari" to get URL of front document',
}

TITLE_SCRIPT = (
    'tell application "System Events" to get title of first window '
    "of (first process whose frontmost is true)"
)
DIA_PROFILE_SCRIPT = 'tell application "Dia" to get name of active profile of front window'
FINDER_SCRIPT = (
    'tell application "Finder" to get POSIX path of '
    "(target of front Finder window as alias)"
)


def _run(args, timeout=8):
    import subprocess

    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "osascript error").strip()[:160])
    return (r.stdout or "").strip()


def normalize_domain(url):
    """Solo host, minuscolo, senza www. Senza host -> 'private', vuoto -> None."""
    if not url:
        return None
    host = urlparse(url.strip()).hostname
    if not host:
        return "private"
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _front_asn():
    import subprocess

    r = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5)
    return (r.stdout or "").strip().split()[0]


def _app_info(asn):
    import subprocess

    name, bundle = None, None
    try:
        out = subprocess.run(["lsappinfo", "info", "-only", "name", "-asn", asn],
                             capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"([^"]+)"', out)
        name = m.group(1) if m else None
    except Exception:
        pass
    try:
        out = subprocess.run(["lsappinfo", "info", "-only", "bundleid", "-asn", asn],
                             capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'bundleID="([^"]+)"', out)
        bundle = m.group(1) if m else None
    except Exception:
        pass
    return name, bundle


def _idle_seconds():
    import subprocess

    out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                         capture_output=True, text=True, timeout=5).stdout
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out or "")
    return int(m.group(1)) / 1e9 if m else 0.0


def collect_sample(now_epoch, idle_limit=IDLE_SECS, allow_browser=True):
    sample = {"ts": now_epoch, "app_name": None, "bundle_id": None,
              "kind": "app", "domain": None, "finder_path": None,
              "title": None, "is_idle": 0, "success": "ok", "error_code": None,
              "profile": None}
    try:
        name, bundle = _app_info(_front_asn())
    except Exception as e:
        sample.update(success="denied_or_error", error_code=str(e)[:160])
        return sample
    sample["app_name"], sample["bundle_id"] = name, bundle
    try:
        idle = _idle_seconds() > idle_limit
    except Exception:
        idle = False
    if (name or "") in ("loginwindow", "ScreenSaverEngine"):
        idle = True
    sample["is_idle"] = 1 if idle else 0

    if name in BROWSER_URL_SCRIPT:
        sample["kind"] = "browser"
        if not allow_browser:
            sample.update(success="backoff", domain=None)
        else:
            try:
                url = _run(["osascript", "-e", BROWSER_URL_SCRIPT[name]])
                sample["domain"] = normalize_domain(url) if url else "private"
                if not url:
                    sample["success"] = "no_tab"
            except Exception as e:
                sample.update(success="denied_or_error",
                              error_code=str(e)[:160], domain=None)
            if name == "Dia" and sample["success"] != "denied_or_error":
                try:
                    sample["profile"] = _run(["osascript", "-e", DIA_PROFILE_SCRIPT]) or None
                except Exception:
                    pass  # shortcut: profilo best-effort, il campione resta valido
    elif name == "Finder":
        sample["kind"] = "finder"
        try:
            sample["finder_path"] = _run(["osascript", "-e", FINDER_SCRIPT])
        except Exception as e:
            sample.update(success="denied_or_error", error_code=str(e)[:160])
    try:
        sample["title"] = _run(["osascript", "-e", TITLE_SCRIPT]) or None
    except Exception:
        sample["title"] = None  # shortcut: titolo best-effort, mai bloccare il campione
    return sample


def init_db(path):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(SCHEMA_PATH) as f:
        sql = f.read()
    con = sqlite3.connect(path)
    try:
        con.executescript(sql)
        cols = {r[1] for r in con.execute("PRAGMA table_info(samples)")}
        if "profile" not in cols:  # DB creati prima del profilo Dia: aggiungi, dati intatti
            con.execute("ALTER TABLE samples ADD COLUMN profile TEXT")
            con.commit()
    finally:
        con.close()


def write_samples(db, rows):
    con = sqlite3.connect(os.path.expanduser(db))
    try:
        con.executemany(
            "INSERT INTO samples(ts,app_name,bundle_id,kind,domain,finder_path,"
            "title,is_idle,success,error_code,profile) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("ts"), r.get("app_name"), r.get("bundle_id"),
              r.get("kind", "app"), r.get("domain"), r.get("finder_path"),
              r.get("title"), r.get("is_idle", 0),
              r.get("success", "ok"), r.get("error_code"),
              r.get("profile")) for r in rows])
        con.commit()
    finally:
        con.close()


def active_seconds(db, frm, to, weight=WEIGHT_SECS):
    con = sqlite3.connect(os.path.expanduser(db))
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM samples WHERE ts>=? AND ts<=? AND is_idle=0",
            (frm, to)).fetchone()[0]
    finally:
        con.close()
    return n * weight
