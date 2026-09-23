"""Offline browser regression; install playwright and Chromium to run."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [360, 768, 1440])
def test_theme_navigation_and_save(theme, width):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    config = json.loads((ROOT / 'config.default.json').read_text())
    posts, auth_headers, errors, external = [], [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme=theme)
        page.add_init_script("localStorage.setItem('pymc_jwt_token', 'browser-test-jwt')")
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request):
            nonlocal config
            url = urlparse(request.request.url)
            if url.hostname != 'plugin.test':
                external.append(request.request.url)
                request.abort()
            elif url.path == '/api/plugins/settings':
                auth_headers.append(request.request.headers.get('authorization'))
                if auth_headers[-1] != 'Bearer browser-test-jwt':
                    request.fulfill(status=401)
                    return
                if request.request.method == 'POST':
                    payload = request.request.post_data_json
                    posts.append(payload)
                    config = payload['config']
                request.fulfill(json={'config': config})
            else:
                relative = url.path.removeprefix('/plugins/openhop.prometheus/')
                file = ROOT / 'ui' / relative
                if file.is_file():
                    request.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
                else:
                    request.fulfill(status=404)

        page.route('**/*', route)
        page.goto('http://plugin.test/plugins/openhop.prometheus/index.html')
        page.wait_for_function("document.querySelector('#port').value === '9109'")
        assert page.locator('.prometheus-logo').evaluate('(img) => img.complete && img.naturalWidth > 0')
        expected = 'rgb(5, 7, 13)' if theme == 'dark' else 'rgb(246, 248, 255)'
        assert page.locator('body').evaluate('(el) => getComputedStyle(el).backgroundColor') == expected
        for tab in ('overview', 'metrics', 'collectors', 'prometheus', 'settings'):
            page.locator(f'[data-tab="{tab}"]').click()
            assert page.locator(f'#tab-{tab}').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        buttons = page.locator('.help-button')
        assert buttons.count() == 12
        for button in buttons.all():
            field = page.locator('#' + button.get_attribute('data-field'))
            before = field.evaluate('(el) => [el.value, el.checked]')
            button.click()
            assert button.get_attribute('aria-expanded') == 'true'
            assert page.locator('#' + button.get_attribute('aria-controls')).is_visible()
            assert field.evaluate('(el) => [el.value, el.checked]') == before
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            button.press('Escape')
            assert button.get_attribute('aria-expanded') == 'false'
            button.press('Enter')
            assert button.get_attribute('aria-expanded') == 'true'
            button.press('Space')
            assert button.get_attribute('aria-expanded') == 'false'
            button.locator('.help-indicator').click()
            assert button.get_attribute('aria-expanded') == 'true'
            assert field.evaluate('(el) => [el.value, el.checked]') == before
            page.locator('h1').click()
            assert button.get_attribute('aria-expanded') == 'false'
        page.locator('#port').fill('9110')
        page.locator('#repeater_verify_tls').uncheck()
        page.locator('[data-tab="overview"]').click()
        page.locator('[data-tab="settings"]').click()
        assert page.locator('#port').input_value() == '9110'
        page.locator('button[type="submit"]').click()
        page.wait_for_function("document.querySelector('#notice').textContent.includes('Saved.')")
        assert posts[-1]['config']['port'] == 9110
        assert posts[-1]['config']['repeater_verify_tls'] is False
        assert posts[-1]['restart'] is True
        assert auth_headers and all(value == 'Bearer browser-test-jwt' for value in auth_headers)
        page.reload()
        page.locator('[data-tab="settings"]').click()
        page.wait_for_function("document.querySelector('#port').value === '9110'")
        assert not page.locator('#repeater_verify_tls').is_checked()
        folder = os.environ.get('UI_SCREENSHOTS')
        if folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
            page.locator('#label-repeater_api_token').click()
            page.screenshot(path=f'{folder}/{theme}-{width}-settings.png', full_page=True)
            page.locator('[data-tab="overview"]').click()
            page.screenshot(path=f'{folder}/{theme}-{width}-overview.png', full_page=True)
        assert not errors
        assert not external
        browser.close()


@pytest.mark.parametrize('stored_token,expected', [
    (None, 'Authentication is required.'),
    ('expired-test-jwt', 'session has expired.'),
])
def test_settings_401_explains_dashboard_login(stored_token, expected):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        if stored_token:
            page.add_init_script(f"localStorage.setItem('pymc_jwt_token', {json.dumps(stored_token)})")

        def route(request):
            url = urlparse(request.request.url)
            if url.path == '/api/plugins/settings':
                assert request.request.headers.get('authorization') == (
                    f'Bearer {stored_token}' if stored_token else None
                )
                request.fulfill(status=401)
            else:
                file = ROOT / 'ui' / url.path.removeprefix('/plugins/openhop.prometheus/')
                if file.is_file():
                    request.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
                else:
                    request.fulfill(status=404)

        page.route('**/*', route)
        page.goto('http://plugin.test/plugins/openhop.prometheus/index.html')
        page.wait_for_function("text => document.querySelector('#notice').textContent.includes(text)", arg=expected)
        assert page.locator('#notice').is_visible()
        browser.close()
