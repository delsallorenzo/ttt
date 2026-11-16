"""Vista live come app: logica pura (niente terminale), yaml sempre in tmp_path."""
import os


def _yaml(tmp_path, text):
    p = str(tmp_path / "containers.yaml")
    with open(p, "w") as f:
        f.write(text)
    return p


def test_move_item_adds_and_steals(tmp_path):
    from cli import move_item, load_simple_yaml
    p = _yaml(tmp_path, "a:\n  - {type: domain, value: x.com}\n  - {type: app, value: Figma}\n")
    out = move_item(p, "site", "x.com", "b")
    y = load_simple_yaml(p)
    assert y["b"] == [{"type": "domain", "value": "x.com"}]
    assert y["a"] == [{"type": "app", "value": "Figma"}]
    assert "b" in out
    assert os.listdir(str(tmp_path / "hist"))  # backup fatto


def test_move_item_path_full(tmp_path):
    from cli import move_item, load_simple_yaml
    p = _yaml(tmp_path, "")
    move_item(p, "path", "~/Downloads/new/", "dl")
    assert load_simple_yaml(p)["dl"] == [
        {"type": "path", "value": os.path.expanduser("~/Downloads/new/")}]


def test_move_item_rejects_other(tmp_path):
    from cli import move_item
    p = _yaml(tmp_path, "a:\n  - {type: domain, value: x.com}\n")
    before = open(p).read()
    assert "other" in move_item(p, "site", "x.com", "other")
    assert open(p).read() == before


S = {"sites": [("x.com", 60), ("y.com", 30)], "apps": [("Figma", 90)],
     "paths": [("~/Downloads/", 30)],
     "containers": [("other", 300), ("work", 0)]}
NAMES = ["work"]


def _grid():
    from cli import nav_items, new_state
    st = new_state()
    st["focus"] = "grid"
    return st, nav_items(S)


def test_nav_items_columns():
    from cli import nav_items
    left, right = nav_items(S)
    assert left == [("site", "x.com", 60), ("site", "y.com", 30), ("path", "~/Downloads/", 30)]
    assert right == [("app", "Figma", 90), ("container", "other", 300), ("container", "work", 0)]


def test_date_row_moves_time_and_down_enters_grid():
    from cli import handle_key, nav_items, new_state
    st, cols = new_state(), nav_items(S)
    assert st["focus"] == "date"
    assert handle_key(st, "left", cols, NAMES) == ("period", -1)
    assert handle_key(st, "right", cols, NAMES) == ("period", 1)
    handle_key(st, "down", cols, NAMES)
    assert (st["focus"], st["col"], st["row"]) == ("grid", 0, 0)
    handle_key(st, "up", cols, NAMES)
    assert st["focus"] == "date"


def test_grid_moves_with_limits():
    from cli import handle_key
    st, cols = _grid()
    for _ in range(5):
        handle_key(st, "down", cols, NAMES)
    assert st["row"] == 2  # ultima voce della colonna sinistra
    handle_key(st, "right", cols, NAMES)
    assert (st["col"], st["row"]) == (1, 2)
    handle_key(st, "left", cols, NAMES)
    assert st["col"] == 0


def test_right_on_empty_column_stays():
    from cli import handle_key
    st = _grid()[0]
    cols = [[("site", "x.com", 60)], []]
    handle_key(st, "right", cols, NAMES)
    assert st["col"] == 0


def test_typing_sends_chat_or_command():
    from cli import handle_key
    st, cols = _grid()
    for ch in "ciao":
        handle_key(st, ch, cols, NAMES)
    assert handle_key(st, "enter", cols, NAMES) == ("send", "ciao")
    assert st["input"] == ""
    for ch in "/doctor":
        handle_key(st, ch, cols, NAMES)
    assert handle_key(st, "enter", cols, NAMES) == ("cmd", "/doctor")


def test_enter_on_site_menu_confirm_move():
    from cli import handle_key
    st, cols = _grid()
    assert handle_key(st, "enter", cols, NAMES) is None
    assert st["menu"]["options"] == ["work", "+ new container"]
    handle_key(st, "enter", cols, NAMES)
    assert st["menu"] is None and st["prompt"]["kind"] == "confirm"
    assert "x.com" in st["prompt"]["text"] and "work" in st["prompt"]["text"]
    assert handle_key(st, "s", cols, NAMES) == ("move", ("site", "x.com", 60), "work")
    assert st["prompt"] is None


def test_confirm_no_cancels():
    from cli import handle_key
    st, cols = _grid()
    handle_key(st, "enter", cols, NAMES)
    handle_key(st, "enter", cols, NAMES)
    assert handle_key(st, "n", cols, NAMES) is None
    assert st["prompt"] is None


def test_new_container_asks_name_then_confirms():
    from cli import handle_key
    st, cols = _grid()
    handle_key(st, "enter", cols, NAMES)
    handle_key(st, "down", cols, NAMES)
    handle_key(st, "enter", cols, NAMES)
    assert st["prompt"]["kind"] == "name"
    for ch in "blog":
        handle_key(st, ch, cols, NAMES)
    handle_key(st, "enter", cols, NAMES)
    assert st["prompt"]["kind"] == "confirm"
    assert handle_key(st, "s", cols, NAMES) == ("move", ("site", "x.com", 60), "blog")


def test_enter_on_container_renames_but_not_other():
    from cli import handle_key
    st, cols = _grid()
    st["col"], st["row"] = 1, 1  # other
    assert handle_key(st, "enter", cols, NAMES) is None
    assert st["prompt"] is None and st["menu"] is None
    st["row"] = 2  # work
    handle_key(st, "enter", cols, NAMES)
    assert st["prompt"]["kind"] == "name"
    for ch in "studio":
        handle_key(st, ch, cols, NAMES)
    handle_key(st, "enter", cols, NAMES)
    assert handle_key(st, "s", cols, NAMES) == ("rename", "work", "studio")


def test_rename_rejects_existing_or_empty():
    from cli import handle_key
    st, cols = _grid()
    st["col"], st["row"] = 1, 2
    handle_key(st, "enter", cols, NAMES)
    handle_key(st, "enter", cols, NAMES)  # nome vuoto
    assert st["prompt"]["kind"] == "name"
    for ch in "work":
        handle_key(st, ch, cols, NAMES)
    handle_key(st, "enter", cols, NAMES)
    assert st["prompt"]["kind"] == "name"  # esiste gia': resta a chiedere


def test_esc_layers_and_quit():
    from cli import handle_key
    st, cols = _grid()
    handle_key(st, "enter", cols, NAMES)
    handle_key(st, "esc", cols, NAMES)
    assert st["menu"] is None
    handle_key(st, "a", cols, NAMES)
    handle_key(st, "esc", cols, NAMES)
    assert st["input"] == ""
    assert handle_key(st, "esc", cols, NAMES) == ("today",)
    assert handle_key(st, "ctrl-c", cols, NAMES) == ("quit",)
    assert handle_key(st, "tab", cols, NAMES) == ("tab",)


def test_chat_confirm_prompt_answers():
    from cli import handle_key
    st, cols = _grid()
    st["prompt"] = {"kind": "confirm", "text": "add x", "effect": ("answer",)}
    assert handle_key(st, "s", cols, NAMES) == ("answer", True)
    st["prompt"] = {"kind": "confirm", "text": "add x", "effect": ("answer",)}
    assert handle_key(st, "esc", cols, NAMES) == ("answer", False)


PARTS = ("today · Mon Nov 16", S)
FOOT = "ttt · total 5m"


def test_render_marks_selected_item():
    from cli import render_app
    st, _ = _grid()
    st["row"] = 1
    lines = render_app(PARTS, st, 100, 30, FOOT).splitlines()
    row = next(l for l in lines if "y.com" in l)
    assert "▸" in row
    assert not any("▸" in l for l in lines if "x.com" in l)


def test_render_date_focus_and_finder_marker():
    from cli import render_app, new_state
    out = render_app(PARTS, new_state(), 100, 30, FOOT)
    assert out.splitlines()[0].startswith("‹ today") and "▸" not in out
    st, _ = _grid()
    st["row"] = 2  # prima voce finder
    lines = render_app(PARTS, st, 100, 30, FOOT).splitlines()
    assert "▸" in next(l for l in lines if "~/Downloads/" in l)


def test_render_menu_prompt_chat_and_footer():
    from cli import render_app
    st, _ = _grid()
    st["menu"] = {"options": ["work", "+ new container"], "sel": 1,
                  "item": ("site", "x.com", 60)}
    st["chat"] = [f"line{i}" for i in range(40)]
    out = render_app(PARTS, st, 100, 30, FOOT)
    lines = out.splitlines()
    assert len(lines) == 30
    assert any("x.com" in l and "to:" in l for l in lines)
    assert any(l.strip().startswith("› + new container") for l in lines)
    assert "line0" not in out and "line39" in out and "line36" in out  # usa lo spazio libero
    assert lines[-1].startswith("↑↓←→") and lines[-2] == FOOT
    assert lines[-3].startswith("› ")
    st["menu"], st["prompt"] = None, {"kind": "confirm", "text": "move 1m x.com → work?",
                                      "effect": ("move",)}
    assert "move 1m x.com → work? [s/N]" in render_app(PARTS, st, 100, 30, FOOT)


def test_render_narrow_terminal_keeps_marker():
    from cli import render_app
    st, _ = _grid()
    st["col"], st["row"] = 1, 0
    out = render_app(PARTS, st, 30, 40, FOOT)
    assert "▸" in next(l for l in out.splitlines() if "Figma" in l)


def test_render_chat_min_three_lines_when_full():
    from cli import render_app
    st, _ = _grid()
    st["chat"] = ["l1", "l2", "l3", "l4"]
    out = render_app(PARTS, st, 100, 5, FOOT)  # terminale bassissimo
    assert "l2" in out and "l4" in out


def test_read_key_names():
    from cli import KeyReader, read_key
    r, w = os.pipe()
    os.write(w, "\x1b[Ab\r\t\x7fè".encode())
    k = KeyReader(r)
    assert [read_key(k, 0.05) for _ in range(6)] == ["up", "b", "enter", "tab", "backspace", "è"]
    os.write(w, b"\x1b")
    assert read_key(k, 0.05) == "esc"
    assert read_key(k, 0.01) is None
    os.close(r)
    os.close(w)


def test_chat_worker_confirm_roundtrip(tmp_path):
    import time as _t
    from cli import ChatWorker, load_simple_yaml
    cpath = _yaml(tmp_path, "a:\n  - {type: domain, value: x.com}\n")
    before = open(cpath).read()
    calls = []

    def fake(messages, cfg):
        calls.append(1)
        if len(calls) == 1:
            return '```action\n{"tool": "add_rules", "container": "a", "rules": [{"type": "app", "value": "Figma"}]}\n```'
        return "Ok, annullato."

    w = ChatWorker({}, {"db": str(tmp_path / "none.db"), "containers": cpath, "days": 7}, complete=fake)
    w.send("aggiungi figma ad a")
    ev = w.out.get(timeout=2)
    assert ev[0] == "confirm" and "Figma" in ev[1]
    w.answers.put(False)
    ev = w.out.get(timeout=2)
    assert ev == ("reply", "Ok, annullato.")
    assert open(cpath).read() == before


def test_empty_past_day_label_is_that_day(tmp_path):
    import time as _t
    from cli import live_parts
    from collect import init_db, write_samples
    db = str(tmp_path / "e.db")
    init_db(db)
    now = int(_t.time())
    write_samples(db, [{"ts": now, "is_idle": 0, "app_name": "Dia", "success": "ok"}])
    parts, msg = live_parts(db, "daily", now, offset=-3)
    day = _t.strftime("%a %b %d", _t.localtime(now - 3 * 86400))
    assert parts is None and day in msg and "today" not in msg


def test_long_paths_keep_the_last_folder():
    from cli import _columns
    p = "~/Library/CloudStorage/Drive/projects/work/2026/client-a/"
    out = _columns([("finder", [(p, 60)])], 80)
    assert "client-a/" in out and "…" in out
    assert "client-a.com" in _columns([("sites", [("client-a.com", 60)])], 80)
