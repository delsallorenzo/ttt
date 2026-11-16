"""Wizard interattivo per settings. Niente rete vera, tasti finti."""
import io
import json


class FakeKeys:
    def __init__(self, seq):
        self.seq = list(seq)

    def getkey(self):
        return self.seq.pop(0) if self.seq else None

    def poll(self, timeout=None):
        return self.seq.pop(0) if self.seq else None

    def clear(self):
        self.seq = []


def test_pick_option_arrows_wrap_and_cancel():
    from cli import pick_option
    out = io.StringIO()
    opts = [("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")]
    assert pick_option(FakeKeys(["\x1b", "[", "B", "\r"]), out, "t", opts) == 1
    assert pick_option(FakeKeys(["\x1b", "[", "A", "\r"]), out, "t", opts) == 2
    assert pick_option(FakeKeys(["\x1b", "[", "B", "\r"]), out, "t", opts, initial=2) == 0
    assert pick_option(FakeKeys(["\x1b"]), out, "t", opts) is None


def test_read_secret_masks_and_backspace():
    from cli import read_secret
    out = io.StringIO()
    assert read_secret(FakeKeys(["a", "b", "\x7f", "c", "\r"]), out, "key") == "ac"
    assert "ac" not in out.getvalue() and "**" in out.getvalue()
    assert read_secret(FakeKeys(["\x1b"]), out, "key") is None


def test_pick_model_arrows():
    from cli import pick_model
    out = io.StringIO()
    models = ["zzz/cheap", "aaa/pro", "mmm/mid"]
    got = pick_model(FakeKeys(["\x1b", "[", "B", "\r"]), out, models)
    assert got == "aaa/pro"


def test_pick_model_manual_entry():
    from cli import pick_model
    out = io.StringIO()
    assert pick_model(FakeKeys(list("mymodel") + ["\r"]), out, []) == "mymodel"
    assert pick_model(FakeKeys(["\x1b"]), out, []) is None


def test_fetch_models_shapes(monkeypatch):
    import urllib.request
    from cli import fetch_models
    seen = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"data": [{"id": "b/2"}, {"id": "a/1"}]}).encode()

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    got = fetch_models("openrouter", "", "")
    assert got == ["a/1", "b/2"] and seen["url"].endswith("/models")


def test_wizard_full_flow_saves(tmp_path):
    from cli import settings_wizard, load_config
    out = io.StringIO()
    cfg_path = str(tmp_path / "ttt.conf")
    seq = ["\x1b", "[", "B", "\x1b", "[", "B", "\r"]  # openrouter
    seq += list("sk-test") + ["\r"]  # chiave
    seq += ["\r"]  # primo modello della lista
    seq += ["\r"]  # conferma
    msg = settings_wizard(FakeKeys(seq), out, cfg_path,
                          fetch=lambda p, b, k: ["aaa/1", "bbb/2"])
    cfg = load_config(cfg_path)
    assert cfg["provider"] == "openrouter" and cfg["model"] == "aaa/1"
    assert cfg["api_key"] == "sk-test"
    assert "sk-test" not in out.getvalue()
    assert "saved" in msg


def test_wizard_esc_cancels(tmp_path):
    from cli import settings_wizard
    import os
    out = io.StringIO()
    cfg_path = str(tmp_path / "ttt.conf")
    msg = settings_wizard(FakeKeys(["\x1b"]), out, cfg_path)
    assert "unchanged" in msg and not os.path.exists(cfg_path)
