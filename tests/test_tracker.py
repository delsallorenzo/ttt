from collect import active_seconds, init_db, write_samples

from tracker import note_denied, should_query_browser


def test_fixed_weight_no_delta(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db)
    write_samples(db, [{"ts": 100, "is_idle": 0}, {"ts": 500, "is_idle": 0}])
    assert active_seconds(db, 0, 999) == 10  # 2 campioni x 5s, non 400s


def test_backoff_window():
    st = {"until": 0}
    assert should_query_browser(st, 100) is True
    note_denied(st, 100, 60)
    assert should_query_browser(st, 120) is False
    assert should_query_browser(st, 200) is True
