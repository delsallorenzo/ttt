"""Matching context/project in lettura. Solo stdlib."""


def _hits(rules, sample):
    domain = (sample.get("domain") or "").lower()
    path = (sample.get("finder_path") or "").lower()
    app = (sample.get("app_name") or "").lower()
    title = (sample.get("title") or "").lower()
    profile = (sample.get("profile") or "").lower()
    out = []
    for name, rs in (rules or {}).items():
        for r in rs:
            v = (r.get("value") or "").lower()
            if not v:
                continue
            t = r.get("type")
            if t == "domain" and v in domain:
                out.append((name, v))
            elif t == "path" and v in path:
                out.append((name, v))
            elif t == "app" and v in app:
                out.append((name, v))
            elif t == "title_contains" and v in title:
                out.append((name, v))
            elif t == "profile" and v == profile:
                out.append((name, v, True))
    return out


def _pick(hits):
    # Un profilo Dia vince su tutto; poi la regola piu' lunga; a parita' la prima nel file.
    profiles = [h for h in hits if len(h) == 3]
    if profiles:
        return profiles[0][0]
    best, best_len = None, -1
    for name, v in hits:
        if len(v) > best_len:
            best, best_len = name, len(v)
    return best


def apply_rules(sample, contexts, projects):
    """Returns (project, context). Priority: project > context > other."""
    project = _pick(_hits(projects, sample)) or "other"
    context = _pick(_hits(contexts, sample)) or "other"
    return project, context
