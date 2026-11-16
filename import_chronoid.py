"""One-off: importa gli activity_spans di Chronoid nel nostro usage.db.

Ogni span diventa N campioni da 5s (N = max(1, round(durata/5))) distribuiti
su [start, end]. Rieseguibile: cancella prima le righe marcate import:chronoid.
Solo stdlib. Uso: python3 import_chronoid.py <backup.sqlite> [usage.db]
"""
import os
import sqlite3
import sys
from urllib.parse import urlparse

MARK = "import:chronoid"
WEIGHT = 5

SRC_SQL = """
SELECT s.start_time, s.end_time, s.duration_seconds,
       a.app_name, a.bundle_identifier, d.domain_name,
       p.full_path, w.title_text, s.is_idle
FROM activity_spans s
JOIN applications a ON a.app_id = s.app_id
LEFT JOIN domains d ON d.domain_id = s.domain_id
LEFT JOIN tracked_paths p ON p.path_id = s.file_path_id
LEFT JOIN window_titles w ON w.window_title_id = s.window_title_id
WHERE s.deleted_at IS NULL AND s.duration_seconds > 0
ORDER BY s.start_time
"""


def norm_domain(name):
    if not name:
        return None
    host = urlparse(name if "://" in name else "https://" + name).hostname or name
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def main(backup, dest):
    dest = os.path.expanduser(dest)
    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        dst.execute("DELETE FROM samples WHERE error_code=?", (MARK,))
        dst.commit()
        cur = src.execute(SRC_SQL)
        batch, total_spans, total_rows = [], 0, 0
        for start, end, dur, app, bundle, dom, path, title, idle in cur:
            n = max(1, round(dur / WEIGHT))
            step = dur / n
            kind = "app"
            domain = norm_domain(dom)
            if domain:
                kind = "browser"
            elif (app or "") == "Finder":
                kind = "finder"
            for i in range(n):
                batch.append((int(start + i * step), app, bundle, kind, domain,
                              path if kind == "finder" else None, title,
                              1 if idle else 0, "ok", MARK))
            total_spans += 1
            total_rows += n
            if len(batch) >= 5000:
                dst.executemany(
                    "INSERT INTO samples(ts,app_name,bundle_id,kind,domain,"
                    "finder_path,title,is_idle,success,error_code)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                dst.commit()
                batch = []
                print(f"  ...{total_spans} span, {total_rows} campioni", flush=True)
        if batch:
            dst.executemany(
                "INSERT INTO samples(ts,app_name,bundle_id,kind,domain,"
                "finder_path,title,is_idle,success,error_code)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
            dst.commit()
        print(f"fatto: {total_spans} span -> {total_rows} campioni")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2
         else os.path.expanduser("~/.usage/usage.db"))
