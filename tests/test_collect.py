from collect import collect_sample, normalize_domain


def test_collect_sample_keys():
    s = collect_sample(1763078400)
    assert set(s) == {"ts", "app_name", "bundle_id", "kind", "domain",
                      "finder_path", "title", "is_idle", "success", "error_code", "profile"}
    assert s["ts"] == 1763078400


def test_domain_is_host_only():
    assert normalize_domain("https://www.github.com/org/repo?q=1") == "github.com"


def test_private_window():
    assert normalize_domain("about:blank-private") == "private"


def test_init_db_adds_profile_to_old_db(tmp_path):
    import sqlite3
    from collect import init_db, write_samples
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE samples(ts INTEGER NOT NULL, app_name TEXT, bundle_id TEXT, kind TEXT,"
                " domain TEXT, finder_path TEXT, title TEXT, is_idle INTEGER NOT NULL DEFAULT 0,"
                " success TEXT NOT NULL DEFAULT 'ok', error_code TEXT)")
    con.execute("INSERT INTO samples(ts, app_name) VALUES(1, 'Dia')")
    con.commit()
    con.close()
    init_db(db)
    init_db(db)  # idempotente
    write_samples(db, [{"ts": 2, "app_name": "Dia", "profile": "Work"}])
    con = sqlite3.connect(db)
    assert con.execute("SELECT ts, profile FROM samples ORDER BY ts").fetchall() == [(1, None), (2, "Work")]
    con.close()


def test_dia_sample_has_profile(monkeypatch):
    import collect
    monkeypatch.setattr(collect, "_front_asn", lambda: "ASN")
    monkeypatch.setattr(collect, "_app_info", lambda asn: ("Dia", "company.thebrowser.dia"))
    monkeypatch.setattr(collect, "_idle_seconds", lambda: 0)

    def fake_run(args, timeout=8):
        script = args[-1]
        if "active profile" in script:
            return "Work"
        if "URL" in script:
            return "https://slack.com/x"
        return "title"
    monkeypatch.setattr(collect, "_run", fake_run)
    s = collect.collect_sample(1)
    assert s["profile"] == "Work" and s["domain"] == "slack.com" and s["success"] == "ok"

    def broken(args, timeout=8):
        if "active profile" in args[-1]:
            raise RuntimeError("no")
        return fake_run(args)
    monkeypatch.setattr(collect, "_run", broken)
    s = collect.collect_sample(1)
    assert s["profile"] is None and s["success"] == "ok"
