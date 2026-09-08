from pathlib import Path


def test_ui_has_required_tabs_and_controls():
    root = Path(__file__).resolve().parents[1]
    html = (root / "ui" / "index.html").read_text()
    for tab in ("overview", "metrics", "collectors", "prometheus", "settings"):
        assert f'data-tab="{tab}"' in html
    assert 'id="test-repeater"' in html
    assert 'id="prom-test-endpoint"' in html
    assert 'id="refresh-now"' in html
    assert "MeshCore" not in html
    assert "system collector" not in html.lower()


def test_ui_uses_authenticated_openhop_settings_api():
    root = Path(__file__).resolve().parents[1]
    js = (root / "ui" / "app.js").read_text()
    assert 'const API = "/api/plugins/settings"' in js
    assert 'pymc_jwt_token' in js
    assert 'Authorization' in js
    assert 'Bearer ${token}' in js


def test_ui_cleans_removed_v01_config_keys():
    root = Path(__file__).resolve().parents[1]
    js = (root / "ui" / "app.js").read_text()
    assert "supportedConfig" in js
    assert "meshcore_enabled" not in js
    assert "system_enabled" not in js


def test_ui_warns_about_external_bind_and_does_not_render_token_runtime():
    root = Path(__file__).resolve().parents[1]
    js = (root / "ui" / "app.js").read_text()
    html = (root / "ui" / "index.html").read_text()
    assert "no application-level authentication" in js.lower()
    assert 'type="password"' in html
    assert "api_token_configured" not in html
