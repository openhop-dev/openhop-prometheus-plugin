from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_official_header_logo_is_bundled():
    html = (ROOT / 'ui/index.html').read_text()
    assert 'src="assets/prometheus-logo.svg"' in html
    assert 'class="brand-mark"' not in html
    assert (ROOT / 'ui/assets/prometheus-logo.svg').is_file()
    package = (ROOT / 'pyproject.toml').read_text()
    assert '"ui/assets/prometheus-logo.svg"' in package
    assert '"ui/assets/PROMETHEUS-LICENSE"' in package


def test_nomad_palette_and_controls():
    css = (ROOT / 'ui/styles.css').read_text()
    for token in ('--bg: #f6f8ff;', '--accent: #3b82f6;', '--bg: #05070d;',
                  '--accent: #60a5fa;', '--panel: #0d121f;'):
        assert token in css
    assert '.brand-mark' not in css
    assert 'button:focus-visible' in css
