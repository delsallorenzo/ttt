"""CLI usage: report + diagnostica. Solo stdlib."""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

from collect import WEIGHT_SECS
from match import apply_rules

DB_DEFAULT = os.path.expanduser("~/.usage/usage.db")
HOME = os.path.expanduser("~")
BASE = os.path.dirname(os.path.abspath(__file__))


def load_simple_yaml(path):
    """Sottoinsieme minimo: `nome:` poi righe `- {type: x, value: y}`."""
    rules, cur = {}, None
    try:
        with open(path) as f:
            for line in f:
            # shortcut: parser rigido sul nostro formato; righe ignote saltate
                m = re.match(r"^(\S[^:]*):\s*$", line)
                if m:
                    cur = m.group(1)
                    rules[cur] = []
                    continue
                m = re.search(r"\{([^}]*)\}", line)
                if m and cur:
                    kv = dict(kv.split(":", 1) for kv in m.group(1).split(","))
                    rules[cur].append({k.strip(): v.strip() for k, v in kv.items()})
    except FileNotFoundError:
        pass
    return rules


def fmt_dur(s):
    """Solo minuti (ed eventualmente ore). 100s -> '2m', 3660s -> '1h01m'."""
    m = int(s / 60 + 0.5)  # arrotonda per eccesso: mai '0m' sopra i 30s
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def day_bounds(which, now=None):
    now = now or time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, -1, -1, -1))
    if which == "daily":
        return midnight, now, "today"
    if which == "weekly":
        start = midnight - lt.tm_wday * 86400
        return start, now, "last 7 days (since Monday)"
    start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, -1, -1, -1))
    return start, now, "this month"


def rows_in(db, frm, to):
    con = sqlite3.connect(os.path.expanduser(db))
    try:
        return con.execute(
            "SELECT ts,app_name,bundle_id,kind,domain,finder_path,title,is_idle,"
            "success,error_code FROM samples WHERE ts>=? AND ts<=?", (frm, to)).fetchall()
    finally:
        con.close()


def home_rel(p):
    """/Users/me/Downloads -> ~/Downloads. Il resto invariato."""
    if not p:
        return p
    home = os.path.expanduser("~")
    if p == home:
        return "~"
    if p.startswith(home + "/"):
        return "~" + p[len(home):]
    return p


def _short(k, n=22):
    """Taglia a n caratteri; i percorsi da sinistra, cosi' resta la cartella finale."""
    if len(k) <= n:
        return k
    return "…" + k[-(n - 1):] if k[:1] in ("~", "/") else k[:n]


def _columns(blocks, width, gap=3, split_at=None, sel=None):
    """Blocchi [(titolo, [(nome, secondi)])] affiancati in colonne allineate.
    split_at: la seconda colonna parte da questa colonna (default: dopo gap).
    sel: (blocco, riga) da marcare con ▸ (la durata e' allineata a destra: c'e' spazio)."""
    cols = []
    for b, (title, items) in enumerate(blocks):
        cells = [title] + [f"{fmt_dur(v):>7}  {_short(k)}" for k, v in items]
        if sel and sel[0] == b and 0 <= sel[1] < len(items):
            c = cells[sel[1] + 1]
            i = max(0, len(c) - len(c.lstrip(" ")) - 2)  # attaccato alla durata
            cells[sel[1] + 1] = c[:i] + "▸" + c[i + 1:]
        w = max(len(c) for c in cells)
        cols.append(([c.ljust(w) for c in cells], w))
    total_w = sum(w for _, w in cols) + gap * (len(cols) - 1)
    if width is None or total_w > width:
        # shortcut: terminale stretto, impila invece di stringere
        out = []
        for cells, _ in cols:
            out.extend(cells)
            out.append("")
        return "\n".join(out).rstrip()
    if split_at is not None and len(cols) >= 2:
        # la seconda colonna inizia esattamente a split_at (meno il gap)
        target = split_at - gap
        w1 = cols[1][1]
        if cols[0][1] < target and target + gap + w1 <= (width or target + gap + w1):
            cells, _ = cols[0]
            cols[0] = ([c.ljust(target) for c in cells], target)
    height = max(len(c) for c, _ in cols)
    lines = []
    for i in range(height):
        lines.append((" " * gap).join(
            cells[i] if i < len(cells) else " " * w for cells, w in cols).rstrip())
    return "\n".join(lines)


def view_range(view, now, offset=0):
    """(frm, to) del periodo. offset<0 = periodi passati (freccia sinistra)."""
    if view == "weekly":
        frm, _to, _label = day_bounds("weekly", now)
        frm += offset * 7 * 86400
        return frm, (now if offset == 0 else frm + 7 * 86400 - 1)
    if view == "monthly":
        lt = time.localtime(now)
        m = lt.tm_mon - 1 + offset
        y = lt.tm_year + m // 12
        m = m % 12 + 1
        start = time.mktime((y, m, 1, 0, 0, 0, -1, -1, -1))
        nm = m % 12 + 1
        ny = y + (1 if m == 12 else 0)
        nstart = time.mktime((ny, nm, 1, 0, 0, 0, -1, -1, -1))
        return start, (now if offset == 0 else nstart - 1)
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, -1, -1, -1))
    frm = midnight + offset * 86400
    return frm, (now if offset == 0 else frm + 86399)


def summarize_range(db, frm, to, ctx, prj, project=None, unmapped_only=False):
    """Aggregati via SQL (GROUP BY sulle combo distinte): veloce anche su 1M righe.
    project = filtra su un container (report --container).
    unmapped_only = solo righe che finiscono in 'other' (report unmapped)."""
    con = sqlite3.connect(os.path.expanduser(db))
    try:
        combos = con.execute(
            "SELECT kind, domain, finder_path, app_name, title, is_idle, profile, COUNT(*)"
            " FROM samples WHERE ts>=? AND ts<=?"
            " GROUP BY kind, domain, finder_path, app_name, title, is_idle, profile",
            (int(frm), int(to))).fetchall()
    finally:
        con.close()
    total, n = 0, 0
    sites, apps, paths, pc, cc = {}, {}, {}, {}, {}
    for kind, domain, path, app, title, idle, profile, count in combos:
        if idle:
            continue
        secs = count * WEIGHT_SECS
        p, c = apply_rules({"domain": domain, "finder_path": path,
                            "app_name": app, "title": title, "profile": profile}, ctx, prj)
        if project is not None and p != project:
            continue
        if unmapped_only and p != "other":
            continue
        total += secs
        n += count
        if kind == "browser" and domain:
            sites[domain] = sites.get(domain, 0) + secs
        # in unmapped il tempo browser con dominio e' gia' fra i siti: non sommarlo all'app
        if app and not (unmapped_only and kind == "browser" and domain):
            apps[app] = apps.get(app, 0) + secs
        if kind == "finder" and path:
            k = home_rel(path)
            paths[k] = paths.get(k, 0) + secs
        pc[p] = pc.get(p, 0) + secs
        cc[c] = cc.get(c, 0) + secs
    for name in (prj or {}):
        pc.setdefault(name, 0)  # tutti i container, anche a 0m
    keep = lambda d: sorted(((k, v) for k, v in d.items() if v >= 30),
                            key=lambda kv: -kv[1])
    return {
        "total": total, "n": n,
        "sites": keep(sites), "apps": keep(apps), "paths": keep(paths),
        "containers": sorted(pc.items(), key=lambda kv: -kv[1]),
        "contexts": sorted(cc.items(), key=lambda kv: -kv[1]),
    }


def view_label(view, frm, to, now, offset=0):
    """Etichetta della vista: oggi data, settimana range, mese nome."""
    if view == "weekly":
        sun = frm + 6 * 86400
        a = time.strftime("%a %b %-d", time.localtime(frm))
        b = time.strftime("%a %b %-d", time.localtime(sun))
        return f"{a} - {b}"
    if view == "monthly":
        return time.strftime("%B", time.localtime(frm))
    day = time.strftime("%a %b %d", time.localtime(min(to, now)))
    return f"{'today' if offset == 0 else 'day'} · {day}"


def live_parts(db, view, now, offset=0):
    """Dati per un frame: (topline, summary) o (None, msg)."""
    db = os.path.expanduser(db)
    if not os.path.exists(db):
        return None, "DB empty: daemon hasn't written yet"
    frm, to = view_range(view, now, offset)
    ctx = load_simple_yaml(os.path.join(BASE, "contexts.yaml"))
    prj = load_simple_yaml(os.path.join(BASE, "containers.yaml"))
    s = summarize_range(db, int(frm), int(to), ctx, prj)
    if not s["n"]:
        label = view_label(view if view != "live" else "daily", frm, to, now, offset)
        return None, f"‹ {label} ›: no samples"
    topline = view_label(view if view != "live" else "daily", frm, to, now,
                         offset)
    return (topline, s), ""


PERIODS = ["daily", "weekly", "monthly"]


def next_period(view):
    """Tab gira solo fra i periodi; a live si torna con esc."""
    if view in PERIODS:
        return PERIODS[(PERIODS.index(view) + 1) % len(PERIODS)]
    return "daily"


def _table(title, items):
    lines = [f"== {title} =="]
    for k, v in items:
        lines.append(f"  {fmt_dur(v):>8}  {k}")
    return "\n".join(lines) if len(lines) > 1 else f"== {title} ==\n  (empty)"


def containers_path():
    return os.path.join(BASE, "containers.yaml")


RULE_TYPES = ("domain", "path", "app", "title_contains", "profile")
FLAG2TYPE = {"--domain": "domain", "--path": "path",
             "--app": "app", "--title": "title_contains", "--profile": "profile"}


def containers_list(path=None):
    """Una riga per container con conteggio regole."""
    rules = load_simple_yaml(path or containers_path())
    if not rules:
        return "no containers yet (containers add NAME --domain ... to create one)"
    def plural(n):
        return f"{n} rule{'s' if n != 1 else ''}"
    return "\n".join(f"{n} ({plural(len(rs))})" for n, rs in rules.items())


def _write_containers(path, current):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for n, rs in current.items():
            f.write(f"{n}:\n")
            for r in rs:
                f.write(f"  - {{type: {r['type']}, value: {r['value']}}}\n")
    os.replace(tmp, path)  # scrittura atomica: mai yaml a meta'


def containers_add(path, name, rules):
    """Aggiunge regole a un container (lo crea se manca). Ritorna un messaggio."""
    path = path or containers_path()
    name = (name or "").strip()
    if not name or ":" in name or "\n" in name:
        return "usage: containers add NAME --domain D [--path P] [--app A] [--title T] [--profile P]"
    parsed = []
    for t, v in rules:
        if t not in RULE_TYPES:
            return f"unknown type '{t}': use domain, path, app, title_contains or profile"
        v = (v or "").strip()
        if not v or any(c in v for c in ",{}[]\n"):
            return f"unsupported value '{v}': avoid ,{{}}[] and newlines"
        parsed.append({"type": t, "value": v})
    if not parsed:
        return "usage: containers add NAME --domain D [--path P] [--app A] [--title T] [--profile P]"
    current = load_simple_yaml(path)
    current.setdefault(name, [])
    current[name] += [r for r in parsed if r not in current[name]]
    _write_containers(path, current)
    n = len(current[name])
    return f"{name} now has {n} rule{'s' if n != 1 else ''}"


def containers_rm(path, name):
    """Cancella un container intero. Ritorna un messaggio."""
    path = path or containers_path()
    current = load_simple_yaml(path)
    if name not in current:
        return f"no container '{name}'"
    del current[name]
    _write_containers(path, current)
    return f"removed container '{name}'"


ITEM2RULE = {"site": "domain", "app": "app", "path": "path"}


def move_item(cpath, kind, key, container):
    """Sposta una voce: regola esatta nel target, tolta (identica) dagli altri."""
    name = (container or "").strip()
    if name == "other":
        return "'other' is where unmapped time goes: pick a real container"
    t = ITEM2RULE.get(kind)
    if not t:
        return f"can't move a {kind}"
    v = os.path.expanduser(key) if t == "path" else key
    _backup_containers(cpath)
    msg = containers_add(cpath, name, [(t, v)])
    if "now has" not in msg:
        return msg
    rule = {"type": t, "value": v.strip()}
    current = load_simple_yaml(cpath)
    for n in current:
        if n != name:
            current[n] = [r for r in current[n] if r != rule]
    _write_containers(cpath, current)
    return f"moved {key} to '{name}'"


def containers_cmd(args, path=None):
    """Dispatch di `ttt containers ...`."""
    args = list(args)
    sub = args.pop(0) if args and not args[0].startswith("-") else "ls"
    if sub in ("ls", "list"):
        return containers_list(path)
    if sub == "undo":
        return containers_undo(path)
    if sub == "rm":
        if not args:
            return "usage: containers rm NAME"
        return containers_rm(path, args[0])
    if sub == "add":
        if not args:
            return "usage: containers add NAME --domain D [--path P] [--app A] [--title T] [--profile P]"
        name, rest = args[0], args[1:]
        rules, i = [], 0
        while i < len(rest):
            if rest[i] in FLAG2TYPE and i + 1 < len(rest):
                rules.append((FLAG2TYPE[rest[i]], rest[i + 1]))
                i += 2
            else:
                return "usage: containers add NAME --domain D [--path P] [--app A] [--title T] [--profile P]"
        return containers_add(path, name, rules)
    return "usage: containers [ls|add NAME ...|rm NAME]"


SETTINGS_KEYS = ("provider", "model", "api_key", "api_base")


def config_path():
    return os.path.expanduser("~/.usage/ttt.conf")


def load_config(path=None):
    """Chiave=valore per riga. Mai loggare api_key."""
    cfg = {"provider": "anthropic", "model": "", "api_key": "",
           "api_base": ""}
    try:
        with open(path or config_path()) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in cfg:
                        cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg


def _save_config(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for k in SETTINGS_KEYS:
            if cfg.get(k):
                f.write(f"{k}={cfg[k]}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def config_set(path, key, value):
    """Scrive un setting (file 0600: contiene la chiave)."""
    path = path or config_path()
    if key not in SETTINGS_KEYS:
        return f"unknown setting '{key}': use provider, model, api_key, api_base"
    value = (value or "").strip()
    if key == "provider" and value not in ("anthropic", "openai", "openrouter"):
        return "provider must be anthropic, openai or openrouter"
    cfg = load_config(path)
    cfg[key] = value
    _save_config(path, cfg)
    extra = " (or export TTT_API_KEY to keep it out of shell history)" if key == "api_key" else ""
    return f"{key} saved{extra}"


def effective_key(cfg):
    """Env vince sul file: niente chiave nella history della shell."""
    return os.environ.get("TTT_API_KEY") or cfg.get("api_key", "")


def config_display(cfg):
    key = cfg.get("api_key", "")
    masked = "••••" + key[-2:] if len(key) > 4 else "(not set)"
    env = " (TTT_API_KEY overrides file)" if os.environ.get("TTT_API_KEY") else ""
    return "\n".join([
        f"provider={cfg.get('provider')}",
        f"model={cfg.get('model') or '(not set)'}",
        f"api_base={cfg.get('api_base') or '(default)'}",
        f"api_key={masked}{env}",
    ])


class AIError(Exception):
    pass


def ai_chat(messages, cfg):
    """Chat multi-turno via stdlib. messages: [{role, content}]."""
    provider = cfg.get("provider", "anthropic")
    model = (cfg.get("model") or "").strip()
    key = effective_key(cfg)
    if not key:
        raise AIError("no API key: ttt settings set api_key ... (or TTT_API_KEY env)")
    if not model:
        raise AIError("no model: run ttt settings first")
    base = provider_base(provider, cfg.get("api_base"))
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [{"role": m["role"], "content": m["content"]}
            for m in messages if m["role"] != "system"]
    if provider == "anthropic":
        url = base + "/v1/messages"
        body = {"model": model, "max_tokens": 2000, "system": system,
                "messages": rest}
        headers = {"Content-Type": "application/json", "x-api-key": key,
                   "anthropic-version": "2023-06-01"}
    else:  # openai e openrouter parlano lo stesso protocollo
        url = base.rstrip("/") + "/chat/completions"
        body = {"model": model, "temperature": 0,
                "messages": ([{"role": "system", "content": system}] if system else [])
                + rest}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        if "openrouter" in base:  # richiesti per le analytics di OpenRouter
            headers["HTTP-Referer"] = "ttt/usage-tracker"
            headers["X-Title"] = "ttt time tracker"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        hint = "invalid key?" if e.code in (401, 403) else f"HTTP {e.code}"
        raise AIError(f"API error: {hint} (check ttt settings)")
    except Exception as e:
        raise AIError(f"network error: {e}")
    try:
        if provider == "anthropic":
            return data["content"][0]["text"]
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("unexpected API response shape")


def provider_base(provider, api_base):
    """Base URL di default per provider (override con api_base)."""
    if api_base:
        return api_base
    if provider == "anthropic":
        return "https://api.anthropic.com"
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    return "https://api.openai.com/v1"


def fetch_models(provider, base, key=""):
    """Lista modelli dal provider (OpenRouter e' pubblica, altri con chiave)."""
    base = provider_base(provider, base)
    headers = {}
    if provider == "anthropic":
        url, headers = base + "/v1/models", {"anthropic-version": "2023-06-01"}
        if key:
            headers["x-api-key"] = key
    else:
        url = base.rstrip("/") + "/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return sorted({m["id"] for m in data["data"]})
    except urllib.error.HTTPError as e:
        raise AIError(f"model list error: HTTP {e.code} (wrong key?)")
    except AIError:
        raise
    except Exception as e:
        raise AIError(f"model list error: {e}")


def pick_option(keys, stdout, title, options, initial=0):
    """Menu a frecce: ritorna l'indice o None (esc)."""
    sel = max(0, min(initial, len(options) - 1)) if options else 0
    while True:
        menu = "\n".join(f"{'›' if i == sel else ' '} {label}"
                           for i, (_v, label) in enumerate(options))
        stdout.write("\033[2J\033[H" + title + "\n" + menu
                     + "\n[↑↓] choose · [enter] confirm · [esc] cancel")
        stdout.flush()
        ch = keys.getkey()
        if ch in ("\r", "\n"):
            return sel
        if ch == "\x1b":
            if keys.poll(0.05) == "[":
                c3 = keys.poll(0.05) or ""
                if c3 == "A":
                    sel = move_sel(sel, -1, len(options))
                elif c3 == "B":
                    sel = move_sel(sel, 1, len(options))
                else:
                    keys.clear()
            else:
                keys.clear()
                return None


def read_secret(keys, stdout, prompt):
    """Input mascherato (****). Ritorna il testo o None (esc)."""
    buf = []
    while True:
        stdout.write("\033[2J\033[H" + prompt + "*" * len(buf) + "█")
        stdout.flush()
        ch = keys.getkey()
        if ch in ("\r", "\n"):
            return "".join(buf)
        if ch == "\x1b":
            keys.clear()
            return None
        if ch in ("\x7f", "\x08"):
            if buf:
                buf.pop()
            continue
        if ch and ch.isprintable():
            buf.append(ch)


def pick_model(keys, stdout, models, note=""):
    """Cerca+frecce sul modello. Lista vuota = inserimento libero."""
    buf, sel = [], 0
    while True:
        text = "".join(buf).lower()
        matches = [m for m in models if text in m.lower()][:8]
        sel = max(0, min(sel, len(matches) - 1)) if matches else 0
        lines = [note] if note else []
        lines += [f"{'›' if i == sel else ' '} {m}" for i, m in enumerate(matches)]
        if not matches:
            lines.append("  (no match — enter uses what you typed)" if models
                         else "  (type the model id, enter to confirm)")
        hint = f"\nmodel: {''.join(buf)}█"
        stdout.write("\033[2J\033[H" + "\n".join(lines) + hint
                     + "\n[↑↓] choose · [enter] confirm · [esc] cancel")
        stdout.flush()
        ch = keys.getkey()
        if ch in ("\r", "\n"):
            if matches:
                return matches[sel]
            return "".join(buf) or None
        if ch == "\x1b":
            if keys.poll(0.05) == "[":
                c3 = keys.poll(0.05) or ""
                if c3 == "A":
                    sel = move_sel(sel, -1, len(matches))
                elif c3 == "B":
                    sel = move_sel(sel, 1, len(matches))
                else:
                    keys.clear()
            else:
                keys.clear()
                return None
            continue
        if ch in ("\x7f", "\x08"):
            if buf:
                buf.pop()
                sel = 0
            continue
        if ch and ch.isprintable():
            buf.append(ch)
            sel = 0


PROVIDERS = [("anthropic", "Anthropic API"),
             ("openai", "OpenAI API"),
             ("openrouter", "OpenRouter (many models)")]


def settings_wizard(keys, stdout, cfg_path=None, fetch=None):
    """Setup guidato: provider → chiave → modello → conferma."""
    fetch = fetch or fetch_models
    cfg = load_config(cfg_path)
    names = [p for p, _l in PROVIDERS]
    initial = names.index(cfg.get("provider")) if cfg.get("provider") in names else 0
    idx = pick_option(keys, stdout, "AI provider:", PROVIDERS, initial)
    if idx is None:
        return "settings unchanged"
    provider = names[idx]
    base = provider_base(provider, cfg.get("api_base"))
    models, note = [], ""
    if provider == "openrouter":  # lista pubblica: modello prima della chiave
        try:
            models = fetch(provider, base, "")
        except AIError as e:
            note = str(e)
    key = read_secret(keys, stdout, "API key (enter keeps current): ")
    if key is None:
        return "settings unchanged"
    if not key:
        key = cfg.get("api_key", "")
    if provider != "openrouter":  # gli altri vogliono la chiave per la lista
        try:
            models = fetch(provider, base, key)
        except AIError as e:
            note = str(e)
    model = pick_model(keys, stdout, models,
                       note or f"{len(models)} models (type to filter):")
    if not model:
        return "settings unchanged"
    masked = "••••" + key[-2:] if len(key) > 4 else "(not set)"
    stdout.write("\033[2J\033[H"
                 + f"provider={provider}\nmodel={model}\napi_key={masked}\n"
                 + "[enter] save · [esc] cancel")
    stdout.flush()
    while True:
        ch = keys.getkey()
        if ch in ("\r", "\n"):
            break
        if ch == "\x1b":
            keys.clear()
            return "settings unchanged"
    config_set(cfg_path, "provider", provider)
    if key:
        config_set(cfg_path, "api_key", key)
    config_set(cfg_path, "model", model)
    return f"saved: provider={provider} model={model} api_key={masked}"


def run_settings_wizard():
    """Wizard da shell: imposta cbreak da solo."""
    import sys
    import termios
    import tty
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return config_display(load_config())
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        return settings_wizard(KeyReader(fd), sys.stdout, None) or ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h\n")


CHAT_SYSTEM = """Sei l'assistente di ttt, un tracker del tempo locale. Rispondi nella lingua dell'utente, conciso.
Gestisci i container (categorie) con UN'azione per turno, in un blocco ```action
(JSON su una riga, poi ``` di chiusura). Tool disponibili:
{"tool": "list_containers"}
{"tool": "show_container", "name": "..."}
{"tool": "show_unmapped", "days": 7}
{"tool": "query_time", "container": "...", "days": 7}
{"tool": "move", "items": [1, 3], "container": "..."}   (numeri dell'ultimo show_unmapped, oppure "items": "all")
{"tool": "add_rules", "container": "...", "rules": [{"type": "domain|path|app|title_contains|profile", "value": "..."}]}
{"tool": "remove_rule", "container": "...", "type": "...", "value": "..."}
{"tool": "remove_container", "name": "..."}
{"tool": "rename_container", "old": "...", "new": "..."}
{"tool": "undo"}
Regole: usa SEMPRE valori esatti visti in show_unmapped (mai inventarli); per
"quei link/quelli" mostra prima la lista numerata e chiedi quali; le scritture
chiedono conferma all'utente e hanno backup (annulla = undo); se il risultato e'
"cancelled by user" non riprovare. Quando la richiesta e' chiara chiama SUBITO il
tool: NON chiedere tu conferma a parole, la chiede il sistema.; se ambiguo chiedi prima
di agire; dopo il risultato spiega in una riga cosa hai fatto."""

def chat_parse_action(text):
    """Estrae l'azione dal blocco ```action."""
    m = re.search(r"```action:?\s*(.*?)```", (text or ""), re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("tool"), str):
        return data
    return None


def history_dir():
    return os.path.expanduser("~/.usage/history")


def _backup_containers(path):
    """Copia pre-modifica; tiene le ultime 20."""
    if not os.path.exists(path):
        return
    d = history_dir()
    os.makedirs(d, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    shutil.copy(path, os.path.join(d, f"containers-{ts}.yaml"))
    files = sorted(f for f in os.listdir(d)
                   if f.startswith("containers-") and f.endswith(".yaml"))
    for f in files[:-20]:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass


def containers_undo(path=None):
    """Ripristina l'ultimo backup."""
    path = path or containers_path()
    try:
        files = sorted(f for f in os.listdir(history_dir())
                       if f.startswith("containers-") and f.endswith(".yaml"))
    except OSError:
        files = []
    if not files:
        return "nothing to undo"
    latest = os.path.join(history_dir(), files[-1])
    shutil.copy(latest, path)
    n = len(load_simple_yaml(path))
    return f"restored {files[-1]} ({n} containers)"


def _write_preview(action):
    """Testo di conferma per le scritture (move ha la sua anteprima con i tempi)."""
    a = action or {}
    tool = a.get("tool")
    if tool == "add_rules":
        rules = ", ".join(f"{r.get('type')} {r.get('value')}" for r in (a.get("rules") or [])
                          if isinstance(r, dict))
        return f"add to '{a.get('container', '')}': {rules}"
    if tool == "remove_rule":
        return f"remove {a.get('type')} {a.get('value')} from '{a.get('container', '')}'"
    if tool == "remove_container":
        return f"delete container '{a.get('name', '')}' and all its rules"
    if tool == "rename_container":
        return f"rename '{a.get('old', '')}' to '{a.get('new', '')}'"
    if tool == "undo":
        return "restore containers.yaml from the last backup"
    return ""


def chat_exec(action, ctx):
    """Esegue un'azione del modello. Ritorna testo per il modello."""
    tool = (action or {}).get("tool")
    cpath, db, days = ctx["containers"], ctx["db"], ctx.get("days", 7)
    now = int(time.time())
    dfrm, _to, _label = day_bounds("daily", now)
    frm = int(dfrm) - (days - 1) * 86400
    prj = load_simple_yaml(cpath)
    confirm = ctx.get("confirm") or (lambda preview: True)
    cancelled = "cancelled by user: nothing changed"
    preview = _write_preview(action)
    if preview and not confirm(preview):
        return cancelled
    if tool == "list_containers":
        return containers_list(cpath)
    if tool == "show_container":
        name = (action.get("name") or "").strip()
        if name not in prj:
            known = ", ".join(prj) or "none"
            return f"no container '{name}'. Existing: {known}"
        ctx_rules = load_simple_yaml(os.path.join(BASE, "contexts.yaml"))
        s = summarize_range(db, now - 30 * 86400, now, ctx_rules, prj, project=name)
        lines = [f"{name}: last 30d {fmt_dur(s['total'])} ({s['n']} samples)"]
        return "\n".join(lines + [f"  - {r.get('type')}: {r.get('value')}"
                                     for r in prj[name]])
    if tool == "show_unmapped":
        d = int(action.get("days") or days)
        ctx_rules = load_simple_yaml(os.path.join(BASE, "contexts.yaml"))
        u = summarize_range(db, int(dfrm) - (d - 1) * 86400, now,
                            ctx_rules, prj, unmapped_only=True)
        items = ([("site", k, v) for k, v in u["sites"][:15]]
                 + [("app", k, v) for k, v in u["apps"][:10]]
                 + [("path", k, v) for k, v in u["paths"][:10]])[:30]
        ctx["last_unmapped"] = items
        if not items:
            return "nothing unmapped: everything is categorized"
        head = f"unmapped last {d}d: {fmt_dur(u['total'])} ({u['n']} samples)"
        return head + "\n" + "\n".join(
            f"{i}. [{t}] {k} ({fmt_dur(v)})" for i, (t, k, v) in enumerate(items, 1))
    if tool == "query_time":
        name = (action.get("container") or action.get("name") or "").strip()
        d = int(action.get("days") or days)
        ctx_rules = load_simple_yaml(os.path.join(BASE, "contexts.yaml"))
        s = summarize_range(db, int(dfrm) - (d - 1) * 86400, now,
                            ctx_rules, prj,
                            project=name if name and name != "other" else None,
                            unmapped_only=(name == "other"))
        return f"{name or 'total'} last {d}d: {fmt_dur(s['total'])} ({s['n']} samples)"
    if tool == "add_rules":
        name = action.get("container", "")
        pairs = [(r.get("type"), r.get("value")) for r in (action.get("rules") or [])
                 if isinstance(r, dict)]
        _backup_containers(cpath)
        return containers_add(cpath, name, pairs)
    if tool == "move":
        items, name = ctx.get("last_unmapped"), (action.get("container") or "").strip()
        if not items:
            return "nothing to move: call show_unmapped first"
        sel = action.get("items")
        if sel == "all":
            picked = items
        else:
            try:
                idx = [int(i) for i in (sel or [])]
            except (TypeError, ValueError):
                return "items must be a list of numbers or \"all\""
            if not idx or any(i < 1 or i > len(items) for i in idx):
                return f"item out of range: pick 1-{len(items)}"
            picked = [items[i - 1] for i in idx]
        kinds = {"site": "domain", "app": "app", "path": "path"}
        pairs = [(kinds[t], os.path.expanduser(k) if t == "path" else k) for t, k, _ in picked]
        lines = [f"  {kinds[t]} {k} ({fmt_dur(s)})" for t, k, s in picked]
        total = fmt_dur(sum(s for _t, _k, s in picked))
        if not confirm(f"move {total} to '{name}':\n" + "\n".join(lines)):
            return cancelled
        _backup_containers(cpath)
        return containers_add(cpath, name, pairs)
    if tool == "remove_rule":
        name, t = action.get("container", ""), action.get("type")
        v = (action.get("value") or "").strip()
        if name not in prj:
            return f"no container '{name}'"
        before = len(prj[name])
        prj[name] = [r for r in prj[name]
                     if not (r.get("type") == t and (r.get("value") or "").strip() == v)]
        if len(prj[name]) == before:
            known = ", ".join(f"{r.get('type')}:{r.get('value')}" for r in prj[name])
            return f"rule not found in '{name}'. Rules: {known or 'none'}"
        _backup_containers(cpath)
        _write_containers(cpath, prj)
        return f"removed 1 rule from '{name}' ({len(prj[name])} left)"
    if tool == "remove_container":
        _backup_containers(cpath)
        return containers_rm(cpath, action.get("name", ""))
    if tool == "rename_container":
        old, new = (action.get("old") or "").strip(), (action.get("new") or "").strip()
        if old not in prj:
            return f"no container '{old}'"
        if not new or ":" in new:
            return "usage: rename needs a new non-empty name"
        _backup_containers(cpath)
        renamed = {}
        for n, rs in prj.items():
            target = new if n in (old, new) else n
            renamed.setdefault(target, [])
            renamed[target] += [r for r in rs if r not in renamed[target]]
        _write_containers(cpath, renamed)
        return f"renamed '{old}' to '{new}' ({len(renamed[new])} rules)"
    if tool == "undo":
        return containers_undo(cpath)
    return (f"unknown tool '{tool}'. Available: list_containers, show_container, "
            "show_unmapped, query_time, add_rules, remove_rule, remove_container, "
            "rename_container, undo")


def _trim_history(messages, keep=12):
    if len(messages) > keep + 1:
        del messages[1:len(messages) - keep]


def chat_turn(messages, text, ctx, cfg, complete=None):
    """Un turno: utente → modello → (azione → risultato → modello) → risposta."""
    complete = complete or ai_chat
    messages.append({"role": "user", "content": text})
    result = ""
    for _ in range(3):  # shortcut: niente loop infiniti modello↔tool
        reply = complete(messages, cfg)
        action = chat_parse_action(reply)
        if not action:
            messages.append({"role": "assistant", "content": reply})
            _trim_history(messages)
            return reply
        result = chat_exec(action, ctx)
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": f"[tool result]\n{result}"})
    _trim_history(messages)
    return result


NEW_CONTAINER = "+ new container"


def nav_items(s):
    """Voci selezionabili: [sinistra, destra] come (kind, key, secs)."""
    left = ([("site", k, v) for k, v in s["sites"][:5]]
            + [("path", k, v) for k, v in s["paths"][:5]])
    right = ([("app", k, v) for k, v in s["apps"][:5]]
             + [("container", k, v) for k, v in s["containers"]])
    return [left, right]


def new_state():
    return {"focus": "date", "col": 0, "row": 0, "input": "",
            "menu": None, "prompt": None, "chat": []}


def _clamp(st, cols):
    """La selezione resta valida anche se le liste cambiano al refresh."""
    if not cols[st["col"]] and cols[1 - st["col"]]:
        st["col"] = 1 - st["col"]
    st["row"] = max(0, min(st["row"], len(cols[st["col"]]) - 1))


def selected_item(st, cols):
    if st["focus"] != "grid" or not cols[st["col"]]:
        return None
    _clamp(st, cols)
    return cols[st["col"]][st["row"]]


def _ask_confirm(st, effect):
    if effect[0] == "move":
        _k, key, secs = effect[1]
        text = f"move {fmt_dur(secs)} {key} → {effect[2]}?"
    else:
        text = f"rename {effect[1]} → {effect[2]}?"
    st["prompt"] = {"kind": "confirm", "text": text, "effect": effect}


def handle_key(st, key, cols, containers):
    """Macchina a stati della vista app. Modifica st, ritorna un effetto o None."""
    if key == "ctrl-c":
        return ("quit",)
    p = st["prompt"]
    if p and p["kind"] == "confirm":
        yes = key in ("s", "S", "y", "Y")
        if not yes and key not in ("n", "N", "esc", "enter"):
            return None  # default N, ma solo su tasti espliciti
        st["prompt"] = None
        if p["effect"] == ("answer",):
            return ("answer", yes)
        return p["effect"] if yes else None
    if p and p["kind"] == "name":
        if key == "esc":
            st["prompt"] = None
        elif key == "backspace":
            p["buf"] = p["buf"][:-1]
        elif key == "enter":
            name, (what, arg) = p["buf"].strip(), p["then"]
            bad = not name or name == "other" or any(c in name for c in ":,{}[]")
            if bad or (what == "rename" and name in containers):
                p["text"] = "name taken or invalid, try another:"
                return None
            _ask_confirm(st, (what, arg, name))
        elif len(key) == 1 and key.isprintable():
            p["buf"] += key
        return None
    m = st["menu"]
    if m:
        if key == "esc":
            st["menu"] = None
        elif key in ("up", "down"):
            m["sel"] = move_sel(m["sel"], -1 if key == "up" else 1, len(m["options"]))
        elif key == "enter":
            st["menu"], choice = None, m["options"][m["sel"]]
            if choice == NEW_CONTAINER:
                st["prompt"] = {"kind": "name", "text": "new container name:",
                                "buf": "", "then": ("move", m["item"])}
            else:
                _ask_confirm(st, ("move", m["item"], choice))
        return None
    if key == "tab":
        return ("tab",)
    if key == "esc":
        if st["input"]:
            st["input"] = ""
            return None
        return ("today",)
    if key == "backspace":
        st["input"] = st["input"][:-1]
        return None
    if key == "enter":
        text = st["input"].strip()
        st["input"] = ""
        if text:
            return ("cmd", text) if text.startswith("/") else ("send", text)
        item = selected_item(st, cols)
        if not item or item[:2] == ("container", "other"):
            return None
        if item[0] == "container":
            st["prompt"] = {"kind": "name", "text": f"rename {item[1]} to:",
                            "buf": "", "then": ("rename", item[1])}
        else:
            st["menu"] = {"options": [c for c in containers if c != "other"]
                          + [NEW_CONTAINER], "sel": 0, "item": item}
        return None
    if st["focus"] == "date":
        if key in ("left", "right"):
            return ("period", -1 if key == "left" else 1)
        if key == "down" and (cols[0] or cols[1]):
            st["focus"], st["row"] = "grid", 0
            st["col"] = 0 if cols[0] else 1
    elif key in ("up", "down", "left", "right"):
        _clamp(st, cols)
        if key == "up":
            if st["row"] == 0:
                st["focus"] = "date"
            else:
                st["row"] -= 1
        elif key == "down":
            st["row"] = min(st["row"] + 1, len(cols[st["col"]]) - 1)
        else:
            other = 1 if key == "right" else 0
            if cols[other]:
                st["col"] = other
                _clamp(st, cols)
    if len(key) == 1 and key.isprintable():
        st["input"] += key
    return None


def move_sel(sel, delta, n):
    """Sposta la selezione con wrap-around (scroll infinito)."""
    if n <= 0:
        return 0
    return (sel + delta) % n


def period_total(db, view, now, offset=0):
    """(label, total_str, n) del periodo per il footer."""
    frm, to = view_range(view, now, offset)
    label = view_label(view if view != "live" else "daily", frm, to, now,
                       offset)
    rows = rows_in(os.path.expanduser(db), int(frm), int(to))
    n = sum(1 for r in rows if not r[7])
    return label, fmt_dur(n * WEIGHT_SECS), n


def footer_line(db, view, now, offset=0):
    """Due righe: totale+tab sopra, legenda comandi sotto."""
    label, total, n = period_total(db, view, now, offset)
    tabs = " ".join(f"[{v}]" if v == view else v for v in PERIODS)
    return (f"ttt · {label}: active total {total} ({n} samples) · {tabs}"
            f"\n[tab] view · [←→] move · [/] cmds · [q] quit")


def render_frame(db, now=None, width=None, height=None, notice="", view="daily",
               offset=0):
    """Un frame testuale dello stato live. Pura: testabile senza terminale."""
    now = int(now if now is not None else time.time())
    width = width if width is not None else shutil.get_terminal_size().columns
    parts, msg = live_parts(db, view, now, offset)
    if parts is None:
        return msg
    (topline, s), _msg = parts, msg
    footer = footer_line(db, view, now, offset)
    half = (width // 2) if width else None
    row1 = _columns([("sites", s["sites"][:5]), ("apps", s["apps"][:5])],
                    width, split_at=half)
    row2 = _columns([("finder", s["paths"][:5]), ("containers", s["containers"])],
                    width, split_at=half)
    lines = [topline]
    if notice:
        lines.append(notice)
    lines += (row1 + "\n\n\n\n" + row2).splitlines()
    if height is not None:
        pad = height - len(lines) - 2  # footer ora su due righe
        if pad > 0:
            lines += [""] * pad
    lines += footer.splitlines()
    return "\n".join(lines)


APP_LEGEND = "↑↓←→ select · enter actions · tab period · esc back · ctrl-c quit"


def render_app(parts, st, width, height, footer):
    """Frame della vista app: griglia selezionabile, menu, chat, input. Pura."""
    topline, s = parts
    cols = nav_items(s)
    _clamp(st, cols)
    block_sel = {0: None, 1: None}  # riga1 (sites|apps) e riga2 (finder|containers)
    if st["focus"] == "grid" and cols[st["col"]]:
        first = len(s["sites"][:5]) if st["col"] == 0 else len(s["apps"][:5])
        r = st["row"]
        block_sel = ({0: (st["col"], r), 1: None} if r < first
                     else {0: None, 1: (st["col"], r - first)})
    half = (width // 2) if width else None
    row1 = _columns([("sites", s["sites"][:5]), ("apps", s["apps"][:5])],
                    width, split_at=half, sel=block_sel[0])
    row2 = _columns([("finder", s["paths"][:5]), ("containers", s["containers"])],
                    width, split_at=half, sel=block_sel[1])
    top = f"‹ {topline} ›" if st["focus"] == "date" else topline
    lines = [top] + (row1 + "\n\n" + row2).splitlines()
    m = st["menu"]
    if m:
        lines += ["", f"move {m['item'][1]} to:"]
        lines += [f"{'›' if i == m['sel'] else ' '} {o}" for i, o in enumerate(m["options"])]
    p = st["prompt"]
    if p and p["kind"] == "confirm":
        entry = f"{p['text']} [s/N] █"
    elif p:
        entry = f"{p['text']} {p['buf']}█"
    else:
        entry = f"› {st['input']}█"
    room = max(3, (height or 0) - len(lines) - 3)  # la chat usa lo spazio libero, minimo 3 righe
    bottom = [c[:width] if width else c for c in st["chat"][-room:]]
    bottom += [entry, footer.splitlines()[0], APP_LEGEND]
    pad = height - len(lines) - len(bottom) if height else 0
    return "\n".join(lines + [""] * max(0, pad) + bottom)


def palette_action(cmd):
    """Map a /-command to run_cli argv. [] = back to live."""
    parts = cmd.strip().split()
    if not parts or parts[0] == "live":
        return []
    if parts[0] in ("daily", "weekly", "monthly", "doctor", "tail", "help"):
        return [parts[0]]
    if parts[0] == "container" and len(parts) > 1:
        return ["daily", "--container", parts[1]]
    if parts[0] == "unmapped":
        return ["unmapped"]
    if parts[0] == "containers":
        return ["containers"] + parts[1:]
    if parts[0] == "settings":
        if len(parts) == 1:
            return ["settings", "--show"]  # in palette solo lettura
        return ["settings"] + parts[1:]
    if parts[0] in ("quit", "q", "exit"):
        return ["quit"]
    return ["unknown"]


def help_text():
    return """ttt — Terminal Time Tracker

usage: ttt [command] [options]

  (no args)          the app: live view + chat (keys below)
  daily|weekly|monthly   reports (default: today)
    --container NAME   only one container (e.g. ttt daily --container work)
  unmapped [--days N]  where 'other' time goes (default: today)
  containers         list containers and rule counts
  containers add NAME --domain D [--path P] [--app A] [--title T] [--profile P]
  containers rm NAME delete a container
  containers undo    restore containers.yaml before the last change
  settings           AI setup for the chat: provider, model, API key
  settings set K V   non-interactive (provider|model|api_key|api_base)
  tail               last 20 samples
  doctor             check tracker, permissions, errors
  prune [--days N]   delete samples older than N days (default 365)
  tracker on|off     start/stop the background tracker (LaunchAgent)
  menubar on|off     show today's total in the menu bar
  help               this text

keys in the app: ↑↓←→ select (on the date row ← → move in time),
enter on a site/app/folder = move it to a container, enter on a container = rename,
type + enter = chat with AI, /command + enter = run a command (e.g. /unmapped),
tab = switch period, esc = back / clear / today, ctrl-c or /quit = quit"""


class KeyReader:
    """Tasti da un fd in cbreak, con buffer: i byte arrivati insieme non si perdono."""
    def __init__(self, fd):
        import codecs
        self.fd = fd
        self.buf = b""
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")

    def _fill(self):
        import os
        try:
            chunk = os.read(self.fd, 16)
        except OSError:
            return False
        if not chunk:
            return False
        self.buf += chunk
        return True

    def getkey(self):
        """Blocca finche' non c'e' un carattere; "" solo su fd chiuso."""
        while not self.buf:
            if not self._fill():
                return ""
        ch, self.buf = self.buf[:1], self.buf[1:]
        return self.dec.decode(ch, False)

    def clear(self):
        self.buf = b""
        self.dec.reset()

    def poll(self, timeout):
        """Un tasto, o None se scade il timeout. Non perde i byte in buffer."""
        import select
        if not self.buf:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if not r:
                return None
            self._fill()
        return self.getkey()


KEYNAMES = {"\r": "enter", "\n": "enter", "\t": "tab", "\x7f": "backspace",
            "\x08": "backspace", "\x03": "ctrl-c"}
ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}


def read_key(keys, timeout=0.05):
    """Un tasto come nome (up/enter/esc/...) o carattere; None se scade."""
    ch = keys.poll(timeout)
    while ch == "" and keys.buf:  # utf-8 multibyte: il decoder aspetta il resto
        ch = keys.getkey()
    if not ch:
        return None
    if ch == "\x1b":
        c2 = keys.poll(0.05)
        if c2 is None:
            return "esc"
        if c2 in ("[", "O"):
            c3 = keys.poll(0.05) or ""
            if c3 in ARROWS:
                return ARROWS[c3]
        keys.clear()  # sequenza ignota (canc, F-keys): scarta
        return None
    return KEYNAMES.get(ch, ch)


class ChatWorker:
    """Chat su un thread: la vista non si blocca. Eventi in out: ("reply", testo)
    o ("confirm", anteprima) -> rispondi con answers.put(bool)."""
    def __init__(self, cfg, ctx, complete=None):
        import queue
        import threading
        self.cfg, self.complete = cfg, complete
        self.ctx = dict(ctx, confirm=self._confirm)
        self.inbox, self.out, self.answers = queue.Queue(), queue.Queue(), queue.Queue()
        self.messages = [{"role": "system", "content": CHAT_SYSTEM}]
        threading.Thread(target=self._loop, daemon=True).start()

    def send(self, text):
        self.inbox.put(text)

    def _confirm(self, preview):
        self.out.put(("confirm", preview))
        return self.answers.get()

    def _loop(self):
        while True:
            text = self.inbox.get()
            try:
                reply = chat_turn(self.messages, text, self.ctx, self.cfg, self.complete)
            except Exception as e:  # AIError, rete, risposte strane: mai far morire il thread
                reply = f"error: {e}"
            self.out.put(("reply", reply))


def _paint(stdout, frame):
    """Ridisegna sopra senza cancellare tutto: niente sfarfallio a ogni tasto."""
    stdout.write("\033[H" + "\n".join(l + "\033[K" for l in frame.split("\n")) + "\033[J")
    stdout.flush()


def _chat_lines(text, who):
    rows = [l for l in (text or "").splitlines() if l.strip()] or ["(empty reply)"]
    return [f"{who}: {rows[0]}"] + [f"     {l}" for l in rows[1:]]


def run_live(db=None):
    """La vista e' l'app: frecce per scegliere, enter per agire, chat sempre attiva.
    Fuori da un TTY: un frame singolo."""
    import sys
    db = os.path.expanduser(db or DB_DEFAULT)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(render_frame(db))
        return
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    st = new_state()
    view, offset = "daily", 0
    cfg = load_config()
    worker = None
    if effective_key(cfg) and (cfg.get("model") or "").strip():
        worker = ChatWorker(cfg, {"db": db, "containers": containers_path(), "days": 7})
    else:
        st["chat"].append("AI off: run ttt settings (arrows and enter still work)")
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?25l\033[2J")  # cursore nascosto: il █ fa da cursore
        keys = KeyReader(fd)
        parts, msg = None, ""
        last_size, last_load, dirty = None, 0, True
        while True:
            now = time.time()
            while worker and not worker.out.empty():
                kind, payload = worker.out.get()
                if st["chat"] and st["chat"][-1] == "ai: …":
                    st["chat"].pop()
                if kind == "reply":
                    st["chat"] += _chat_lines(payload, "ai")
                    last_load = 0  # la chat puo' aver cambiato i container
                else:
                    st["prompt"] = {"kind": "confirm", "effect": ("answer",),
                                    "text": " ".join(payload.split())}
                dirty = True
            size = shutil.get_terminal_size()
            if now - last_load >= WEIGHT_SECS:
                parts, msg = live_parts(db, view, int(now), offset)
                last_load, dirty = now, True
            if dirty or (size.columns, size.lines) != last_size:
                if parts is None:
                    frame = f"{msg}\n\n{APP_LEGEND}"
                else:
                    frame = render_app(parts, st, size.columns, size.lines,
                                       footer_line(db, view, int(now), offset))
                _paint(sys.stdout, frame)
                last_size, dirty = (size.columns, size.lines), False
            key = read_key(keys)
            if key is None:
                continue
            dirty = True
            cols = nav_items(parts[1]) if parts else [[], []]
            names = list(load_simple_yaml(containers_path()))
            eff = handle_key(st, key, cols, names)
            if not eff:
                continue
            what = eff[0]
            if what == "quit":
                break
            if what in ("period", "tab", "today"):
                if what == "period":
                    offset = min(0, offset + eff[1])  # mai nel futuro
                elif what == "tab":
                    view, offset = next_period(view), 0
                else:
                    view, offset = "daily", 0
                last_load = 0
            elif what == "answer":
                worker.answers.put(eff[1])
            elif what == "send":
                st["chat"] += _chat_lines(eff[1], "you")
                if worker:
                    st["chat"].append("ai: …")
                    worker.send(eff[1])
                else:
                    st["chat"].append("AI off: run ttt settings")
            elif what == "move":
                kind, key_, _secs = eff[1]
                st["chat"].append(move_item(containers_path(), kind, key_, eff[2]))
                last_load = 0
            elif what == "rename":
                st["chat"].append(chat_exec(
                    {"tool": "rename_container", "old": eff[1], "new": eff[2]},
                    {"db": db, "containers": containers_path()}))
                last_load = 0
            elif what == "cmd":
                act = palette_action(eff[1][1:])
                if act == ["quit"]:
                    break
                if act == ["unknown"] or not act:
                    st["chat"].append("type to chat · /daily /unmapped /doctor /help /quit")
                    continue
                # shortcut: output a tutto schermo, un tasto per tornare (cbreak, niente readline)
                _paint(sys.stdout, run_cli(act, db=db) + "\n\n[press any key]")
                keys.clear()
                while read_key(keys, 1) is None:
                    pass
                last_load = 0
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h\033[2J\033[H")


AGENTS = {"com.usage.menubar": "menu bar", "com.usage.tracker": "tracker"}


def agent_cmd(label, action, runner=None, dest=None):
    """Installa/avvia (on) o ferma (off) un LaunchAgent dal template nel repo."""
    import sys
    runner = runner or subprocess.run
    dest = dest or os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
    uid = str(os.getuid())
    if action == "on":
        with open(os.path.join(BASE, f"{label}.plist")) as f:
            text = f.read()
        text = (text.replace("__REPO__", BASE).replace("__PYTHON__", sys.executable)
                .replace("__HOME__", os.path.expanduser("~")))
        os.makedirs(os.path.expanduser("~/.usage"), exist_ok=True)
        with open(dest, "w") as f:
            f.write(text)
        runner(["launchctl", "bootstrap", f"gui/{uid}", dest],
               capture_output=True, text=True, timeout=15)
        return f"{AGENTS[label]} on"
    runner(["launchctl", "bootout", f"gui/{uid}", dest],
           capture_output=True, text=True, timeout=15)
    return f"{AGENTS[label]} off"


def menubar_cmd(action, runner=None, dest=None):
    return agent_cmd("com.usage.menubar", action, runner, dest)


def run_cli(argv, db=None):
    db = os.path.expanduser(db or DB_DEFAULT)
    args = list(argv)
    if args in (["help"], ["--help"], ["-h"]):
        return help_text()
    cmd = args.pop(0) if args and not args[0].startswith("-") else "daily"
    proj_filter, days = None, 365
    if "--container" in args:
        proj_filter = args[args.index("--container") + 1]
    if "--days" in args:
        days = int(args[args.index("--days") + 1])

    if cmd in ("menubar", "tracker"):
        action = args[0] if args else ""
        if action not in ("on", "off"):
            return f"usage: ttt {cmd} on|off"
        return agent_cmd(f"com.usage.{cmd}", action)

    if cmd == "prune":
        if not os.path.exists(db):
            return "no DB, nothing to clean"
        cutoff = int(time.time()) - days * 86400
        con = sqlite3.connect(db)
        try:
            n = con.execute("DELETE FROM samples WHERE ts<?", (cutoff,)).rowcount
            con.commit()
        finally:
            con.close()
        return f"removed {n} samples older than {days} days"

    if cmd == "tail":
        if not os.path.exists(db):
            return "DB empty: daemon hasn't written yet"
        rows = rows_in(db, 0, int(time.time()))[-20:]
        return "\n".join(f"{time.strftime('%H:%M', time.localtime(r[0]))} "
                         f"{r[1] or '?'} {r[4] or r[5] or ''}".rstrip() for r in rows) or "(empty)"

    if cmd == "doctor":
        out = []
        out.append(("DB writable", os.path.exists(db) and os.access(db, os.W_OK)))
        try:
            la = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=10).stdout or ""
            out.append(("LaunchAgent loaded", "com.usage.tracker" in la))
        except Exception:
            out.append(("LaunchAgent loaded", False))
        try:
            o = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True,
                               text=True, timeout=5).stdout or ""
            out.append(("idle detection", "HIDIdleTime" in o))
        except Exception:
            out.append(("idle detection", False))
        if os.path.exists(db):
            last = rows_in(db, 0, int(time.time()))[-1:]
            out.append(("last sample <5min", bool(last) and time.time() - last[0][0] < 300))
            errs = [r for r in rows_in(db, int(time.time()) - 3600, int(time.time()))
                    if r[8] not in ("ok", "no_tab", "backoff")]
            out.append((f"errors last hour: {len(errs)}", True))
            if errs:
                out.append((f"  latest: {errs[-1][1]} {errs[-1][9]}", True))
        else:
            out.append(("last sample <5min", False))
        return "\n".join(f"[{'ok' if v else 'FAIL'}] {k}" for k, v in out)

    if cmd == "containers":
        return containers_cmd(args)

    if cmd == "settings":
        if args and args[0] == "set":
            if len(args) != 3:
                return "usage: settings set KEY VALUE (provider|model|api_key|api_base)"
            return config_set(None, args[1], args[2])
        if args == ["--show"]:
            return config_display(load_config())
        if args:
            return "usage: settings [set KEY VALUE]"
        return run_settings_wizard()

    if cmd == "unmapped":
        if not os.path.exists(db):
            return "DB empty: daemon hasn't written yet"
        now = int(time.time())
        ndays = int(args[args.index("--days") + 1]) if "--days" in args else 1
        dfrm, _to, _label = day_bounds("daily", now)
        frm = int(dfrm) - (ndays - 1) * 86400
        ctx = load_simple_yaml(os.path.join(BASE, "contexts.yaml"))
        prj = load_simple_yaml(os.path.join(BASE, "containers.yaml"))
        full = summarize_range(db, frm, now, ctx, prj)
        s = summarize_range(db, frm, now, ctx, prj, unmapped_only=True)
        share = f", {100 * s['total'] // full['total']}% of total" if full["total"] else ""
        head = "other today" if ndays == 1 else f"other last {ndays} days"
        return "\n".join([
            f"{head}: active total {fmt_dur(s['total'])} ({s['n']} samples{share})",
            _table("top unmapped sites", s["sites"][:10]),
            _table("top unmapped apps", s["apps"][:10]),
            _table("top unmapped paths", s["paths"][:10]),
        ])

    # report daily/weekly/monthly
    frm, to, label = day_bounds(cmd if cmd in ("daily", "weekly", "monthly") else "daily")
    if not os.path.exists(db):
        return "DB empty: daemon hasn't written yet"
    ctx = load_simple_yaml(os.path.join(BASE, "contexts.yaml"))
    prj = load_simple_yaml(os.path.join(BASE, "containers.yaml"))
    s = summarize_range(db, int(frm), int(to), ctx, prj, project=proj_filter)
    sites, apps, paths = s["sites"][:10], s["apps"][:10], s["paths"][:10]

    head = f"usage {label}" + (f" [{proj_filter}]" if proj_filter else "")
    return "\n".join([
        f"{head}: active total {fmt_dur(s['total'])} ({s['n']} samples)",
        _table("top sites", sites), _table("top apps", apps),
        _table("top finder paths", paths),
        _table("containers", s["containers"]),
        _table("contexts", s["contexts"]),
    ])
