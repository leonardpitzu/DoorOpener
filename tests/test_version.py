"""The portal's reported version comes from the image, not from a stray file.

A VERSION file at the repo root used to feed this, but it sat outside the
add-on's Docker build context and was never copied in, so the container always
fell back to 0.0.0 — which in turn disabled the portal's own update check.
config.yaml is now the single source and reaches the app as APP_VERSION.
"""
import importlib


def _reload_app_version(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("APP_VERSION", raising=False)
    else:
        monkeypatch.setenv("APP_VERSION", value)
    import app

    return importlib.reload(app).APP_VERSION


def test_version_comes_from_the_environment(monkeypatch):
    assert _reload_app_version(monkeypatch, "2.5.6") == "2.5.6"


def test_version_falls_back_when_unset(monkeypatch):
    assert _reload_app_version(monkeypatch, None) == "0.0.0"


def test_a_stray_version_file_does_not_win(monkeypatch, tmp_path):
    """The exact regression: a VERSION file must not be consulted at all."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "VERSION").write_text("9.9.9")

    assert _reload_app_version(monkeypatch, "2.5.6") == "2.5.6"
