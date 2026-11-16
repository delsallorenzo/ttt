import os

from cli import menubar_cmd


class FakeRes:
    returncode = 0
    stdout = ""
    stderr = ""


def fake_runner_factory(calls):
    def fake(*a, **k):
        calls.append(a)
        return FakeRes()
    return fake


def test_menubar_on_installs_and_bootstraps(tmp_path):
    calls = []
    dest = str(tmp_path / "com.usage.menubar.plist")
    msg = menubar_cmd("on", runner=fake_runner_factory(calls), dest=dest)
    assert os.path.exists(dest)
    assert calls and "bootstrap" in calls[0][0]
    assert "on" in msg


def test_menubar_off_bootouts(tmp_path):
    calls = []
    dest = str(tmp_path / "com.usage.menubar.plist")
    msg = menubar_cmd("off", runner=fake_runner_factory(calls), dest=dest)
    assert calls and "bootout" in calls[0][0]
    assert "off" in msg


def test_agents_install_with_real_paths(tmp_path):
    import sys
    from cli import BASE, agent_cmd
    for label in ("com.usage.menubar", "com.usage.tracker"):
        dest = str(tmp_path / f"{label}.plist")
        agent_cmd(label, "on", runner=fake_runner_factory([]), dest=dest)
        text = open(dest).read()
        assert "__" not in text and BASE in text
        assert os.path.expanduser("~/.usage/") in text
    assert sys.executable in open(str(tmp_path / "com.usage.tracker.plist")).read()
