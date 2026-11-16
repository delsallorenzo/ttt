import pytest


@pytest.fixture(autouse=True)
def _isolated_history(tmp_path, monkeypatch):
    # I backup dei test non devono mai finire in ~/.usage/history (undo reale).
    import cli
    monkeypatch.setattr(cli, "history_dir", lambda: str(tmp_path / "hist"))
