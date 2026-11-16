"""Fase 1: loop manuale. unmapped + CRUD containers, tutto locale."""
import os
import time


def _seed_mixed(db_path):
    from collect import init_db, write_samples
    init_db(db_path)
    now = int(time.time())
    write_samples(db_path, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"} for i in range(12)
    ] + [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "random-blog.xyz", "success": "ok"} for i in range(12)
    ])
    return now


def test_summarize_range_unmapped_only(tmp_path):
    from cli import summarize_range
    db = str(tmp_path / "u.db")
    now = _seed_mixed(db)
    ctx = {"work": [{"type": "domain", "value": "github.com"}]}
    prj = {"work": [{"type": "domain", "value": "github.com"}]}
    s = summarize_range(db, now - 3600, now, ctx, prj, unmapped_only=True)
    assert s["total"] == 60 and s["n"] == 12
    assert s["sites"] == [("random-blog.xyz", 60)]
    assert ("work", 0) in s["containers"]


def test_unmapped_report(tmp_path, monkeypatch):
    import cli
    from cli import run_cli
    from collect import write_samples
    (tmp_path / "containers.yaml").write_text("acme:\n  - {type: title_contains, value: work}\n")
    monkeypatch.setattr(cli, "BASE", str(tmp_path))
    db = str(tmp_path / "ur.db")
    now = _seed_mixed(db)
    # 'work' nel titolo mappa ad acme: non deve comparire fra gli unmapped
    write_samples(db, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "misc-work.com", "title": "work board",
         "success": "ok"} for i in range(12)
    ])
    out = run_cli(["unmapped"], db=db)
    assert "other today:" in out and "random-blog.xyz" in out
    assert "misc-work.com" not in out


def test_containers_add_creates(tmp_path):
    from cli import containers_add, load_simple_yaml
    p = str(tmp_path / "c.yaml")
    out = containers_add(p, "work", [("domain", "work.example"),
                                      ("title_contains", "work")])
    assert "work" in out
    assert load_simple_yaml(p) == {
        "work": [{"type": "domain", "value": "work.example"},
                  {"type": "title_contains", "value": "work"}]}


def test_containers_add_appends_and_rm(tmp_path):
    from cli import containers_add, containers_rm, containers_list, load_simple_yaml
    p = str(tmp_path / "c2.yaml")
    containers_add(p, "work", [("domain", "work.example")])
    containers_add(p, "acme", [("app", "Acme")])
    containers_add(p, "work", [("path", "work")])
    assert len(load_simple_yaml(p)["work"]) == 2
    assert "work (2 rules)" in containers_list(p)
    out = containers_rm(p, "acme")
    assert "acme" in out and "acme" not in load_simple_yaml(p)
    assert "no container" in containers_rm(p, "missing")


def test_containers_add_rejects(tmp_path):
    from cli import containers_add
    p = str(tmp_path / "c3.yaml")
    assert "unsupported" in containers_add(p, "x", [("domain", "a,b")])
    assert "unknown type" in containers_add(p, "x", [("bogus", "y")])
    assert not os.path.exists(p)


def test_containers_list_empty(tmp_path):
    from cli import containers_list
    assert "no containers" in containers_list(str(tmp_path / "none.yaml"))


def test_unmapped_apps_exclude_browser_domains(tmp_path):
    from cli import summarize_range
    from collect import init_db, write_samples
    db = str(tmp_path / "d.db")
    init_db(db)
    now = int(time.time())
    write_samples(db, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "random-blog.xyz", "success": "ok"} for i in range(12)
    ] + [
        {"ts": now - 100 - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "", "success": "ok"} for i in range(6)
    ])
    u = dict(summarize_range(db, now - 3600, now, {}, {}, unmapped_only=True)["apps"])
    assert u["Dia"] == 30
    a = dict(summarize_range(db, now - 3600, now, {}, {})["apps"])
    assert a["Dia"] == 90


def test_summarize_uses_profile(tmp_path):
    from cli import summarize_range
    from collect import init_db, write_samples
    db = str(tmp_path / "pr.db")
    init_db(db)
    now = int(time.time())
    write_samples(db, [{"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
                        "domain": "slack.com", "profile": "Work"} for i in range(6)])
    s = summarize_range(db, now - 3600, now, {}, {"work": [{"type": "profile", "value": "Work"}]})
    assert dict(s["containers"])["work"] == 30


def test_containers_add_profile_flag(tmp_path):
    from cli import containers_cmd, load_simple_yaml
    p = str(tmp_path / "c.yaml")
    containers_cmd(["add", "work", "--profile", "Work"], path=p)
    assert load_simple_yaml(p)["work"] == [{"type": "profile", "value": "Work"}]
