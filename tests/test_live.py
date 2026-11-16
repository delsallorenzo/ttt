import time

from cli import KeyReader, home_rel, palette_action, render_frame


def test_footer_shows_tabs(tmp_path):
    db = str(tmp_path / "t.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120)
    total, legend = frame.splitlines()[-2:]
    assert "daily" in total and "weekly" in total and "monthly" in total
    assert "[tab] view" in legend and "[/]" in legend and "[q]" in legend


def test_report_view_frame(tmp_path):
    db = str(tmp_path / "v.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, height=30, view="weekly")
    assert "usage last 7 days" not in frame  # stesso layout colonne, non report
    assert "sites" in frame and "containers" in frame
    assert "[weekly]" in frame
    assert frame.splitlines()[-2].startswith("ttt ·")


def test_weekly_excludes_old_samples(tmp_path):
    from collect import init_db, write_samples
    import time
    db = str(tmp_path / "w.db")
    init_db(db)
    now = int(time.time())
    write_samples(db, [
        {"ts": now, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"},
        {"ts": now - 8 * 86400, "is_idle": 0, "app_name": "Dia",
         "kind": "browser", "domain": "oldsite.com", "success": "ok"},
    ])
    frame = render_frame(db, now=now, width=120, height=30, view="weekly")
    assert "oldsite.com" not in frame
    assert "(1 samples)" in frame
    import os
    home = os.path.expanduser("~")
    assert home_rel(home + "/Downloads/x") == "~/Downloads/x"
    assert home_rel("/tmp/other") == "/tmp/other"
    assert home_rel(None) is None


def test_help_lists_commands():
    from cli import run_cli
    out = run_cli(["--help"])
    for word in ("daily", "weekly", "menubar", "container", "live"):
        assert word in out


def _seed_home_finder(db_path):
    from collect import init_db, write_samples
    import os
    import time
    init_db(db_path)
    now = int(time.time())
    home = os.path.expanduser("~")
    write_samples(db_path, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Finder", "kind": "finder",
         "finder_path": home + "/Downloads", "success": "ok"} for i in range(8)
    ] + [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "example.com", "success": "ok", "title": "x"} for i in range(8)
    ])
    return now


def test_live_shows_finder_with_tilde(tmp_path):
    db = str(tmp_path / "f.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, height=20)
    assert "finder" in frame
    assert "~/Downloads" in frame


def test_containers_all_listed_even_zero(tmp_path, monkeypatch):
    # ogni container del yaml deve apparire anche a 0m
    import cli
    (tmp_path / "containers.yaml").write_text("acme:\n  - {type: domain, value: acme.test}\n")
    monkeypatch.setattr(cli, "BASE", str(tmp_path))
    db = str(tmp_path / "c0.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, height=20)
    assert "acme" in frame and "0m" in frame


def test_keyreader_keeps_burst():
    import os
    r, w = os.pipe()
    os.write(w, b"daily\n")
    kr = KeyReader(r)
    assert "".join(kr.getkey() for _ in range(6)) == "daily\n"
    os.close(r)
    os.close(w)
from collect import init_db, write_samples


def test_render_frame_shows_current_and_totals(tmp_path):
    db = str(tmp_path / "live.db")
    init_db(db)
    now = int(time.time())
    write_samples(db, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"} for i in range(12)
    ])
    frame = render_frame(db, now=now, width=90)
    assert "Dia" in frame and "github.com" in frame
    assert " 1m" in frame  # 12 campioni x 5s = 60s, minuti soltanto
    header = [l for l in frame.splitlines() if "sites" in l and "apps" in l]
    assert header, "sites/apps/containers must share one column-header row"
    footer = frame.splitlines()[-2]
    assert "ttt" in footer and "today ·" in footer and "active total" in footer


def test_second_column_centered_and_rows_spaced(tmp_path):
    db = str(tmp_path / "s.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=80, height=30)
    lines = frame.splitlines()
    hdr = next(l for l in lines if "sites" in l and "apps" in l)
    assert hdr.index("apps") == 40  # seconda colonna a meta' finestra
    fi = next(i for i, l in enumerate(lines) if l.startswith("finder"))
    blanks = 0
    i = fi - 1
    while i >= 0 and not lines[i].strip():
        blanks += 1
        i -= 1
    assert blanks == 3 and lines[i].strip() != ""


def test_footer_anchored_bottom(tmp_path):
    from collect import init_db, write_samples
    import time
    db = str(tmp_path / "h.db")
    init_db(db)
    now = int(time.time())
    write_samples(db, [
        {"ts": now, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"},
    ])
    frame = render_frame(db, now=now, width=90, height=12)
    lines = frame.splitlines()
    assert len(lines) == 12
    assert lines[-2].startswith("ttt ·")


def test_palette_maps_commands():
    assert palette_action("daily") == ["daily"]
    assert palette_action("weekly") == ["weekly"]
    assert palette_action("doctor") == ["doctor"]
    assert palette_action("container work") == ["daily", "--container", "work"]
    assert palette_action("live") == []
    assert palette_action("quit")[0] == "quit"
    assert palette_action("blah")[0] == "unknown"


def test_live_topline_is_date_not_app(tmp_path):
    db = str(tmp_path / "d.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, height=20, view="live")
    top = frame.splitlines()[0]
    assert "today" in top and "[active" not in frame


def test_next_period_cycles_three_views():
    from cli import next_period
    assert next_period("daily") == "weekly"
    assert next_period("weekly") == "monthly"
    assert next_period("monthly") == "daily"
    assert next_period("live") == "daily"


def test_footer_tabs_no_live(tmp_path):
    db = str(tmp_path / "t2.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, view="daily")
    total, legend = frame.splitlines()[-2:]
    assert "[daily]" in total and "[live]" not in total
    assert "[tab] view" in legend and "[/]" in legend and "[q]" in legend


def test_view_range_offset():
    from cli import view_range
    import time
    now = time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, -1, -1, -1))
    frm, to = view_range("daily", now, 0)
    assert frm == midnight and to == now
    frm1, to1 = view_range("daily", now, -1)
    assert frm1 == midnight - 86400 and to1 == frm1 + 86399
    fww, _tw = view_range("weekly", now, 0)
    fw1, tw1 = view_range("weekly", now, -1)
    assert fw1 == fww - 7 * 86400 and tw1 == fw1 + 7 * 86400 - 1
    fm0, _t = view_range("monthly", now, 0)
    fm1, tm1 = view_range("monthly", now, -1)
    assert tm1 == fm0 - 1 and fm1 < fm0


def test_move_sel_wraps():
    from cli import move_sel
    assert move_sel(2, 1, 3) == 0
    assert move_sel(0, -1, 3) == 2
    assert move_sel(1, 1, 3) == 2
    assert move_sel(0, 1, 0) == 0


def test_summarize_range_totals(tmp_path):
    from cli import summarize_range
    from collect import init_db, write_samples
    import time
    db = str(tmp_path / "sr.db")
    init_db(db)
    now = int(time.time())
    write_samples(db, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"} for i in range(12)
    ])
    ctx = {"work": [{"type": "domain", "value": "github.com"}]}
    prj = {"work": [{"type": "domain", "value": "work.example"}]}
    s = summarize_range(db, now - 3600, now, ctx, prj)
    assert s["total"] == 60 and s["n"] == 12
    assert s["sites"] == [("github.com", 60)]
    assert ("work", 0) in s["containers"]


def test_weekly_offset_shows_previous_week(tmp_path):
    from collect import init_db, write_samples
    from cli import view_range
    import time
    db = str(tmp_path / "ow.db")
    init_db(db)
    now = int(time.time())
    frm, _to = view_range("weekly", now, -1)  # lunedi' scorso: sempre in week-1
    write_samples(db, [
        {"ts": now, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"},
    ] + [
        {"ts": int(frm) + 3600 + i * 5, "is_idle": 0, "app_name": "Dia",
         "kind": "browser", "domain": "oldsite.com", "success": "ok"}
        for i in range(8)
    ])
    frame = render_frame(db, now=now, width=120, height=30, view="weekly", offset=-1)
    assert "oldsite.com" in frame and "github.com" not in frame
    assert frame.splitlines()[-2].startswith("ttt ·")


def test_footer_two_lines_with_legend(tmp_path):
    db = str(tmp_path / "f2.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120)
    lines = frame.splitlines()
    assert lines[-2].startswith("ttt ·") and "[daily]" in lines[-2]
    assert "[tab]" in lines[-1] and "[/]" in lines[-1] and "[q]" in lines[-1]


def test_weekly_topline_is_range(tmp_path):
    import re
    import time
    db = str(tmp_path / "wr.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, view="weekly")
    assert re.match(r"\w{3} \w{3} \d{1,2} - \w{3} \w{3} \d{1,2}$",
                    frame.splitlines()[0])


def test_monthly_topline_is_month_name(tmp_path):
    import time
    db = str(tmp_path / "mn.db")
    now = _seed_home_finder(db)
    frame = render_frame(db, now=now, width=120, view="monthly")
    assert frame.splitlines()[0] == time.strftime("%B", time.localtime(now))
