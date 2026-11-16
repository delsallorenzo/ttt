import time

from collect import init_db, write_samples
from cli import run_cli


def seed(db, idle_samples=0, active_samples=0):
    init_db(db)
    now = int(time.time())
    rows = [{"ts": now, "is_idle": 1, "app_name": "Dia", "kind": "browser",
             "domain": "youtube.com", "success": "ok"} for _ in range(idle_samples)]
    rows += [{"ts": now, "is_idle": 0, "app_name": "Dia", "kind": "browser",
              "domain": "github.com", "success": "ok"} for _ in range(active_samples)]
    write_samples(db, rows)


def test_daily_totals_seed_db(tmp_path):
    db = str(tmp_path / "t.db")
    seed(db, idle_samples=10, active_samples=20)  # 20*5s = 100s
    out = run_cli(["daily"], db=db)
    assert "2m" in out  # 20*5s = 100s -> arrotondato ai minuti
