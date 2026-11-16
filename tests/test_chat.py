"""Chat con tool. Azioni testate su file temporanei, mai sul yaml reale."""
import os
import time


def _seed(db_path):
    from collect import init_db, write_samples
    init_db(db_path)
    now = int(time.time())
    write_samples(db_path, [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "random-blog.xyz", "success": "ok"} for i in range(12)
    ] + [
        {"ts": now - i * 5, "is_idle": 0, "app_name": "Dia", "kind": "browser",
         "domain": "github.com", "success": "ok"} for i in range(12)
    ])
    return now


def _ctx(tmp_path, db):
    cpath = str(tmp_path / "containers.yaml")
    with open(cpath, "w") as f:
        f.write("work:\n  - {type: domain, value: github.com}\n")
    return {"db": db, "containers": cpath, "days": 7}


def test_parse_action_block():
    from cli import chat_parse_action
    a = chat_parse_action('ecco:\n```action\n{"tool": "list_containers"}\n```')
    assert a == {"tool": "list_containers"}
    assert chat_parse_action("niente json qui") is None
    assert chat_parse_action('```action\nnot json\n```') is None


def test_exec_list_and_unmapped(tmp_path):
    from cli import chat_exec
    db = str(tmp_path / "c.db")
    now = _seed(db)
    ctx = _ctx(tmp_path, db)
    out = chat_exec({"tool": "list_containers"}, ctx)
    assert "work" in out
    out = chat_exec({"tool": "show_unmapped"}, ctx)
    assert "random-blog.xyz" in out and "github.com" not in out
    assert "1. [site]" in out


def test_exec_add_rename_remove_undo(tmp_path):
    from cli import chat_exec, load_simple_yaml
    db = str(tmp_path / "c2.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    out = chat_exec({"tool": "add_rules", "container": "work",
                     "rules": [{"type": "domain", "value": "random-blog.xyz"}]}, ctx)
    assert "now has 2 rules" in out
    rules = load_simple_yaml(ctx["containers"])
    assert len(rules["work"]) == 2
    out = chat_exec({"tool": "rename_container", "old": "work", "new": "work2"}, ctx)
    assert "work2" in out
    out = chat_exec({"tool": "remove_rule", "container": "work2",
                     "type": "domain", "value": "github.com"}, ctx)
    assert "removed" in out
    out = chat_exec({"tool": "remove_container", "name": "work2"}, ctx)
    assert "removed container" in out
    assert load_simple_yaml(ctx["containers"]) == {}
    # undo ripristina l'ultimo stato (= con work2 senza github.com)
    out = chat_exec({"tool": "undo"}, ctx)
    assert "restored" in out
    assert "work2" in load_simple_yaml(ctx["containers"])


def test_exec_rejects_bad_values(tmp_path):
    from cli import chat_exec
    db = str(tmp_path / "c3.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    out = chat_exec({"tool": "add_rules", "container": "x",
                     "rules": [{"type": "domain", "value": "a,b"}]}, ctx)
    assert "unsupported" in out
    out = chat_exec({"tool": "mystery"}, ctx)
    assert "unknown tool" in out


def test_exec_query_time(tmp_path):
    from cli import chat_exec
    db = str(tmp_path / "c4.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    out = chat_exec({"tool": "query_time", "container": "work", "days": 7}, ctx)
    assert "1m" in out and "12 samples" in out


def test_chat_turn_runs_tool_then_replies(tmp_path):
    from cli import chat_turn
    db = str(tmp_path / "c5.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    cfg = {"provider": "openai", "model": "m", "api_key": "k",
           "api_base": "", "send_titles": "off"}
    calls = []

    def fake_complete(messages, cfg):
        calls.append(len(messages))
        if len(calls) == 1:
            return '```action\n{"tool": "list_containers"}\n```'
        assert "work" in messages[-1]["content"]  # il risultato torna al modello
        return "Hai questi container: work."

    messages = [{"role": "system", "content": "sys"}]
    reply = chat_turn(messages, "cosa ho?", ctx, cfg, fake_complete)
    assert "work" in reply and len(calls) == 2


def test_parse_action_variants():
    from cli import chat_parse_action
    assert chat_parse_action('ok\n```action:\n{"tool": "undo"}\n```') == {"tool": "undo"}
    assert chat_parse_action('```action: {"tool": "undo"}```') == {"tool": "undo"}


def test_move_by_number(tmp_path):
    from cli import chat_exec, load_simple_yaml
    db = str(tmp_path / "m.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    chat_exec({"tool": "show_unmapped"}, ctx)
    out = chat_exec({"tool": "move", "items": [1], "container": "blog"}, ctx)
    assert "blog" in out
    assert {"type": "domain", "value": "random-blog.xyz"} in load_simple_yaml(ctx["containers"])["blog"]


def test_move_all(tmp_path):
    from cli import chat_exec, load_simple_yaml
    db = str(tmp_path / "m2.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    chat_exec({"tool": "show_unmapped"}, ctx)
    chat_exec({"tool": "move", "items": "all", "container": "work"}, ctx)
    vals = [r["value"] for r in load_simple_yaml(ctx["containers"])["work"]]
    assert "random-blog.xyz" in vals and "github.com" in vals


def test_move_errors(tmp_path):
    from cli import chat_exec
    db = str(tmp_path / "m3.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    before = open(ctx["containers"]).read()
    assert "show_unmapped first" in chat_exec({"tool": "move", "items": [1], "container": "x"}, ctx)
    chat_exec({"tool": "show_unmapped"}, ctx)
    assert "out of range" in chat_exec({"tool": "move", "items": [99], "container": "x"}, ctx)
    assert open(ctx["containers"]).read() == before


def test_confirm_refused(tmp_path):
    from cli import chat_exec
    db = str(tmp_path / "k.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    ctx["confirm"] = lambda preview: False
    before = open(ctx["containers"]).read()
    chat_exec({"tool": "show_unmapped"}, ctx)
    assert "cancelled by user" in chat_exec({"tool": "move", "items": [1], "container": "x"}, ctx)
    assert "cancelled by user" in chat_exec({"tool": "add_rules", "container": "x",
                                             "rules": [{"type": "app", "value": "Dia"}]}, ctx)
    assert "cancelled by user" in chat_exec({"tool": "remove_container", "name": "work"}, ctx)
    assert open(ctx["containers"]).read() == before
    assert not os.path.exists(str(tmp_path / "hist"))


def test_confirm_preview(tmp_path):
    from cli import chat_exec
    db = str(tmp_path / "k2.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    seen = []
    ctx["confirm"] = lambda preview: seen.append(preview) or True
    chat_exec({"tool": "show_unmapped"}, ctx)
    chat_exec({"tool": "move", "items": [1], "container": "blog"}, ctx)
    assert "random-blog.xyz" in seen[0] and "1m" in seen[0] and "blog" in seen[0]


def test_move_path_uses_full_path(tmp_path):
    from cli import chat_exec, load_simple_yaml
    db = str(tmp_path / "p.db")
    _seed(db)
    ctx = _ctx(tmp_path, db)
    ctx["last_unmapped"] = [("path", "~/Downloads/new/", 60)]
    chat_exec({"tool": "move", "items": [1], "container": "dl"}, ctx)
    full = os.path.expanduser("~/Downloads/new/")
    assert load_simple_yaml(ctx["containers"])["dl"] == [{"type": "path", "value": full}]
