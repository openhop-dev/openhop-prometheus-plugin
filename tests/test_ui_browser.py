"""Offline browser regression; install playwright and Chromium to run."""
import json
import mimetypes
import os
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from openhop_prometheus_plugin.model import CollectorResult, MetricSample
from openhop_prometheus_plugin.state import MetricsState

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 23, 12, 0, 0, 123456, tzinfo=timezone.utc)
SETTINGS_API = '/api/plugins/settings'
RUNTIME_API = '/api/plugins/runtime'


def runtime_envelope(now=NOW):
    """Use real producer UI types, including fractional UTC timestamps.

    The outer envelope is PluginManager.get_runtime's public contract; the
    metadata/http/repeater/actions fields mirror publish_runtime_snapshot.
    """
    with patch('openhop_prometheus_plugin.state.time.time', return_value=now.timestamp()):
        state = MetricsState('1.0.0')
        state.set_running(True)
        started = state.mark_collection_started()
        state.update_collector(CollectorResult('repeater', True, [
            MetricSample('openhop_repeater_up', 'Repeater connected', 'gauge', 1,
                         collector='repeater'),
        ], 0.012))
        state.mark_collection_completed(started)
        state.note_scrape()
        snapshot = state.ui_snapshot(next_collection_at=(now + timedelta(seconds=30)).timestamp())
    snapshot.update({
        'schema': 1, 'plugin_version': '1.0.0',
        'generated_at': now.isoformat(),
        'expires_at': (now + timedelta(seconds=30)).isoformat(),
        'http': {'bind_host': '127.0.0.1', 'port': 9109,
                 'metrics_path': '/metrics', 'health_path': '/healthz',
                 'external_bind': False, 'listening': True},
        'repeater': {'enabled': True, 'scheme': 'http', 'host': '127.0.0.1',
                     'port': 8000, 'stats_path': '/api/stats', 'api_token_configured': False},
        'actions': {},
    })
    return {'success': True, 'id': 'openhop.prometheus',
            'path': '/var/lib/openhop_repeater/plugins/openhop.prometheus/data/runtime.json',
            'exists': True, 'runtime': snapshot}


class PluginBrowser:
    """Serve the real bundled UI, intercepting only protected host APIs."""

    def __init__(self, page):
        self.page = page
        self.config = json.loads((ROOT / 'config.default.json').read_text())
        self.config['_runtime'] = {'status': 'error', 'metric_count': 999,
                                   'generated_at': NOW.isoformat(), 'last_error': 'LEGACY DATA'}
        self.runtime = runtime_envelope()
        self.statuses = {SETTINGS_API: 200, RUNTIME_API: 200}
        self.requests, self.posts, self.errors, self.external = [], [], [], []
        self.held = set()
        self.pending = []
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.add_init_script("localStorage.setItem('pymc_jwt_token', 'browser-test-jwt')")
        page.route('**/*', self.route)
        page.clock.install(time=NOW)
        page.clock.pause_at(NOW + timedelta(seconds=1))

    def route(self, route):
        request = route.request
        url = urlparse(request.url)
        if url.netloc != 'plugin.test' or url.path in ('/metrics', '/healthz'):
            self.external.append(request.url)
            route.abort()
        elif url.path in (SETTINGS_API, RUNTIME_API):
            self.requests.append({'path': url.path, 'method': request.method,
                                  'query': parse_qs(url.query),
                                  'authorization': request.headers.get('authorization')})
            if request.method == 'POST':
                assert url.path == SETTINGS_API
                payload = request.post_data_json
                self.posts.append(payload)
                if 200 <= self.statuses[url.path] < 300:
                    self.config = deepcopy(payload['config'])
            payload = {'config': deepcopy(self.config)} if url.path == SETTINGS_API else deepcopy(self.runtime)
            response = {'status': self.statuses[url.path], 'json': payload}
            if (url.path, request.method) in self.held:
                self.pending.append((route, response))
            else:
                route.fulfill(**response)
        else:
            file = ROOT / 'ui' / url.path.removeprefix('/plugins/openhop.prometheus/')
            if file.is_file():
                route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
            else:
                route.fulfill(status=404)

    def open(self):
        self.page.goto('http://plugin.test/plugins/openhop.prometheus/index.html')

    def tick(self, milliseconds=5000):
        self.page.clock.run_for(milliseconds)

    def poll_cycles(self, count):
        # Virtual timers do not make Fetch/body completion synchronous. Await
        # each real routed response before advancing the next poll interval;
        # otherwise coalescing correctly combines several accelerated timers.
        for _ in range(count):
            with self.page.expect_response(
                lambda response: urlparse(response.url).path == RUNTIME_API
            ) as response:
                self.tick()
            response.value.finished()
            self.page.evaluate('() => Promise.resolve()')

    def pending_for(self, path, method='GET'):
        return [(route, response) for route, response in self.pending
                if urlparse(route.request.url).path == path and route.request.method == method]

    def wait_pending(self, path, method='GET'):
        deadline = time.monotonic() + 5
        while not self.pending_for(path, method) and time.monotonic() < deadline:
            self.page.wait_for_timeout(10)
        assert self.pending_for(path, method), f'No pending {method} {path}'

    def release(self, path=None, method='GET'):
        item = self.pending_for(path, method)[0] if path else self.pending[0]
        self.pending.remove(item)
        route, response = item
        route.fulfill(**response)


@pytest.fixture
def plugin_browser():
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        harness = PluginBrowser(browser.new_page())
        try:
            yield harness
            assert not harness.errors
            assert not harness.external
        finally:
            harness.held.clear()
            while harness.pending:
                harness.release()
            harness.page.unroute_all(behavior='ignoreErrors')
            browser.close()


def test_runtime_uses_authenticated_volatile_api_and_ignores_legacy_config(plugin_browser):
    ui = plugin_browser
    ui.open()
    page = ui.page
    pytest.importorskip('playwright.sync_api').expect(page.locator('#overview-health')).to_have_text('healthy')
    assert page.locator('#kpi-metrics').inner_text() == '1'
    assert 'LEGACY DATA' not in page.locator('body').inner_text()
    page.locator('[data-tab="metrics"]').click()
    assert page.locator('#metrics-body').inner_text().startswith('openhop_repeater_up')
    ui.poll_cycles(2)
    gets = [request for request in ui.requests if request['method'] == 'GET']
    assert {request['path'] for request in gets} == {SETTINGS_API, RUNTIME_API}
    assert sum(request['path'] == RUNTIME_API for request in gets) >= 3
    assert all(request['authorization'] == 'Bearer browser-test-jwt' for request in gets)
    assert all(request['query'] == {'id': ['openhop.prometheus']} for request in gets)
    assert not ui.posts


def expect_text(page, selector, text):
    pytest.importorskip('playwright.sync_api').expect(page.locator(selector)).to_have_text(text)


def test_polling_never_overlaps_each_api(plugin_browser):
    ui = plugin_browser
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    ui.held.update({(SETTINGS_API, 'GET'), (RUNTIME_API, 'GET')})
    ui.tick(16000)
    ui.wait_pending(SETTINGS_API)
    ui.wait_pending(RUNTIME_API)
    assert len(ui.pending_for(SETTINGS_API)) == 1
    assert len(ui.pending_for(RUNTIME_API)) == 1
    assert not ui.posts


@pytest.mark.parametrize('tab,selector,field,action', [
    ('overview', '#refresh-now', 'refresh_request_id', 'refresh'),
    ('metrics', '#metrics-refresh', 'refresh_request_id', 'refresh'),
    ('overview', '#overview-test-endpoint', 'test_endpoint_request_id', 'endpoint'),
    ('prometheus', '#prom-test-endpoint', 'test_endpoint_request_id', 'endpoint'),
    ('settings', '#test-repeater', 'test_repeater_request_id', 'repeater'),
    ('collectors', '[data-test-collector="repeater"]', 'test_repeater_request_id', 'repeater'),
])
def test_all_explicit_actions_keep_saved_settings_and_render_runtime_results(plugin_browser, tab, selector, field, action):
    ui = plugin_browser
    ui.open()
    page = ui.page
    expect = pytest.importorskip('playwright.sync_api').expect
    expect_text(page, '#overview-health', 'healthy')
    page.locator('[data-tab="settings"]').click()
    page.locator('#port').fill('9222')
    page.locator('#repeater_verify_tls').uncheck()
    ui.tick()
    expect(page.locator('#port')).to_have_value('9222')
    assert not page.locator('#repeater_verify_tls').is_checked()
    assert not ui.posts
    page.locator(f'[data-tab="{tab}"]').click()
    page.locator(selector).click()
    expect(page.locator('#notice')).to_contain_text('Results will appear')
    assert len(ui.posts) == 1
    payload = ui.posts[0]
    assert set(payload) == {'id', 'config', 'restart'}
    assert payload['id'] == 'openhop.prometheus'
    assert payload['restart'] is True
    expected_config = json.loads((ROOT / 'config.default.json').read_text())
    request_id = payload['config'][field]
    assert isinstance(request_id, str) and request_id
    expected_config[field] = request_id
    assert payload['config'] == expected_config
    message = f'{action} result <script>window.unexpectedActionScript = true</script>'
    ui.runtime['runtime']['actions'][action] = {
        'request_id': request_id, 'status': 'ok', 'message': message,
        'completed_at': (NOW + timedelta(seconds=6)).isoformat(), 'detail': {},
    }
    ui.tick(1900)
    expect(page.locator('#test-results')).to_contain_text(message)
    if action == 'endpoint':
        expect(page.locator('#endpoint-test-result')).to_contain_text(message)
    assert not page.locator('#test-results script').count()
    assert page.evaluate('window.unexpectedActionScript === undefined')
    page.locator('[data-tab="settings"]').click()
    expect(page.locator('#port')).to_have_value('9222')
    assert not page.locator('#repeater_verify_tls').is_checked()
    ui.tick(4000)
    assert len(ui.posts) == 1
    assert all(request['authorization'] == 'Bearer browser-test-jwt' for request in ui.requests)


def test_queued_actions_and_save_build_on_latest_committed_request_ids(plugin_browser):
    ui = plugin_browser
    ui.open()
    page = ui.page
    expect = pytest.importorskip('playwright.sync_api').expect
    expect_text(page, '#overview-health', 'healthy')
    ui.held.add((SETTINGS_API, 'POST'))
    page.locator('#overview-test-endpoint').click()
    ui.wait_pending(SETTINGS_API, 'POST')
    page.locator('#refresh-now').click()
    page.locator('[data-tab="settings"]').click()
    page.locator('#port').fill('9113')
    page.locator('button[type="submit"]').click()
    ui.tick(11000)
    assert len(ui.posts) == 1
    assert len(ui.pending_for(SETTINGS_API, 'POST')) == 1
    ui.release(SETTINGS_API, 'POST')
    ui.wait_pending(SETTINGS_API, 'POST')
    assert ui.posts[1]['config']['test_endpoint_request_id'] == ui.posts[0]['config']['test_endpoint_request_id']
    assert ui.posts[1]['config']['refresh_request_id']
    ui.release(SETTINGS_API, 'POST')
    ui.wait_pending(SETTINGS_API, 'POST')
    assert ui.posts[2]['config']['refresh_request_id'] == ui.posts[1]['config']['refresh_request_id']
    assert ui.posts[2]['config']['test_endpoint_request_id'] == ui.posts[0]['config']['test_endpoint_request_id']
    assert ui.posts[2]['config']['port'] == 9113
    page.locator('#port').fill('9222')
    ui.release(SETTINGS_API, 'POST')
    expect(page.locator('#notice')).to_contain_text('Saved.')
    ui.tick(5000)
    expect(page.locator('#port')).to_have_value('9222')
    assert len(ui.posts) == 3


def test_failed_save_keeps_unsaved_form_and_explains_expired_session(plugin_browser):
    ui = plugin_browser
    ui.open()
    page = ui.page
    expect_text(page, '#overview-health', 'healthy')
    page.locator('[data-tab="settings"]').click()
    page.locator('#port').fill('9222')
    ui.statuses[SETTINGS_API] = 401
    page.locator('button[type="submit"]').click()
    expect = pytest.importorskip('playwright.sync_api').expect
    expect(page.locator('#notice')).to_contain_text('session has expired.')
    ui.tick(6000)
    expect(page.locator('#port')).to_have_value('9222')
    assert ui.config['port'] == 9109


def test_save_and_delayed_reloads_preserve_edits_made_after_submit(plugin_browser):
    ui = plugin_browser
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    page = ui.page
    expect = pytest.importorskip('playwright.sync_api').expect
    page.locator('[data-tab="settings"]').click()
    page.locator('#port').fill('9111')
    ui.held.add((SETTINGS_API, 'POST'))
    page.locator('button[type="submit"]').click()
    ui.wait_pending(SETTINGS_API, 'POST')
    page.locator('#port').fill('9222')
    page.locator('#repeater_verify_tls').uncheck()
    ui.release(SETTINGS_API, 'POST')
    expect(page.locator('#notice')).to_contain_text('Saved.')
    ui.tick(1900)
    expect(page.locator('#port')).to_have_value('9222')
    assert not page.locator('#repeater_verify_tls').is_checked()
    page.locator('#port').fill('9333')
    page.locator('[data-tab="overview"]').click()
    ui.tick(3500)
    page.locator('[data-tab="settings"]').click()
    expect(page.locator('#port')).to_have_value('9333')
    assert ui.posts[-1]['config']['port'] == 9111
    assert ui.posts[-1]['config']['repeater_verify_tls'] is True
    page.locator('#reload-settings').click()
    expect(page.locator('#port')).to_have_value('9111')
    assert page.locator('#repeater_verify_tls').is_checked()


def test_reload_intent_waits_for_poll_and_preserves_subsequent_input(plugin_browser):
    ui = plugin_browser
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    page = ui.page
    expect = pytest.importorskip('playwright.sync_api').expect
    ui.held.add((SETTINGS_API, 'GET'))
    ui.tick()
    ui.wait_pending(SETTINGS_API)
    page.locator('[data-tab="settings"]').click()
    page.locator('#port').fill('9222')
    page.locator('#reload-settings').click()
    ui.config['port'] = 9117
    page.locator('#port').fill('9333')
    assert len(ui.pending_for(SETTINGS_API)) == 1
    ui.release(SETTINGS_API)
    ui.wait_pending(SETTINGS_API)
    ui.held.clear()
    ui.release(SETTINGS_API)
    expect(page.locator('#prom-metrics-url')).to_contain_text(':9117/')
    expect(page.locator('#port')).to_have_value('9333')
    page.locator('#reload-settings').click()
    expect(page.locator('#port')).to_have_value('9117')


def test_pre_save_poll_cannot_restore_old_settings_or_runtime(plugin_browser):
    ui = plugin_browser
    ui.open()
    page = ui.page
    expect = pytest.importorskip('playwright.sync_api').expect
    expect_text(page, '#overview-health', 'healthy')
    ui.held.update({(SETTINGS_API, 'GET'), (RUNTIME_API, 'GET')})
    ui.tick()
    ui.wait_pending(SETTINGS_API)
    ui.wait_pending(RUNTIME_API)
    page.locator('[data-tab="settings"]').click()
    page.locator('#port').fill('9115')
    page.locator('button[type="submit"]').click()
    expect(page.locator('#notice')).to_contain_text('Saved.')
    expect_text(page, '#overview-health', 'unavailable')
    ui.runtime['runtime'].update(status='error', last_error='NEW RUNTIME ERROR')
    ui.release(SETTINGS_API)
    ui.release(RUNTIME_API)
    ui.wait_pending(SETTINGS_API)
    ui.wait_pending(RUNTIME_API)
    expect_text(page, '#overview-health', 'unavailable')
    expect(page.locator('#prom-metrics-url')).to_contain_text(':9115/')
    page.locator('#port').fill('9222')
    ui.held.clear()
    ui.release(SETTINGS_API)
    ui.release(RUNTIME_API)
    expect_text(page, '#overview-health', 'error')
    expect(page.locator('#overview-summary')).to_contain_text('NEW RUNTIME ERROR')
    expect(page.locator('#port')).to_have_value('9222')
    expect(page.locator('#prom-metrics-url')).to_contain_text(':9115/')


def test_frozen_runtime_expires_locally_without_another_response(plugin_browser):
    ui = plugin_browser
    ui.config['collection_interval_seconds'] = 3600
    ui.runtime['runtime']['next_collection_at'] = (NOW + timedelta(hours=1)).isoformat()
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    ui.held.add((RUNTIME_API, 'GET'))
    ui.tick(31000)
    expect_text(ui.page, '#overview-health', 'stale')
    assert 'stale' in ui.page.locator('#overview-title').inner_text().lower()
    assert 'expired' in ui.page.locator('#overview-summary').inner_text().lower()
    assert ui.page.locator('#global-status').get_attribute('class') == 'status-pill warn'
    assert ui.page.locator('#next-collection').inner_text() == '—'
    assert not ui.posts


def test_successful_polls_do_not_renew_frozen_snapshot_and_fresh_heartbeat_recovers(plugin_browser):
    ui = plugin_browser
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    ui.poll_cycles(6)
    expect_text(ui.page, '#overview-health', 'stale')
    assert sum(request['path'] == RUNTIME_API for request in ui.requests) >= 6
    ui.runtime = runtime_envelope(NOW + timedelta(seconds=32))
    ui.tick()
    expect_text(ui.page, '#overview-health', 'healthy')
    assert 'expired' not in ui.page.locator('#notice').inner_text().lower()
    assert not ui.posts


def test_missing_runtime_after_healthy_clears_live_data_instead_of_using_legacy_settings(plugin_browser):
    ui = plugin_browser
    ui.config['_runtime']['status'] = 'healthy'
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    ui.runtime.update(exists=False, runtime={})
    ui.tick()
    expect_text(ui.page, '#overview-health', 'unavailable')
    expect_text(ui.page, '#kpi-metrics', '0')
    assert ui.page.locator('#metrics-body tr').count() == 0
    assert ui.page.locator('#collector-cards .collector-state.good').count() == 0
    assert 'LEGACY DATA' not in ui.page.locator('body').inner_text()
    assert not ui.posts


@pytest.mark.parametrize('fault', [
    'no-file', 'empty', 'missing-envelope', 'null', 'array',
    'missing-generated', 'missing-expiry', 'invalid-generated', 'invalid-expiry',
    'numeric-generated', 'no-timezone', 'backwards-expiry', 'future-generated',
    'missing-running', 'missing-status', 'invalid-calendar-date',
    'missing-schema', 'unknown-schema', 'wrong-plugin', 'false-exists-with-data',
])
def test_runtime_rejects_missing_or_invalid_snapshot(plugin_browser, fault):
    ui = plugin_browser
    snapshot = ui.runtime['runtime']
    if fault == 'no-file':
        ui.runtime.update(exists=False, runtime={})
    elif fault == 'empty':
        ui.runtime['runtime'] = {}
    elif fault == 'missing-envelope':
        ui.runtime = snapshot
    elif fault == 'null':
        ui.runtime = None
    elif fault == 'array':
        ui.runtime['runtime'] = []
    elif fault == 'missing-generated':
        snapshot.pop('generated_at')
    elif fault == 'missing-expiry':
        snapshot.pop('expires_at')
    elif fault == 'invalid-generated':
        snapshot['generated_at'] = 'not a timestamp'
    elif fault == 'invalid-expiry':
        snapshot['expires_at'] = 'not a timestamp'
    elif fault == 'numeric-generated':
        snapshot['generated_at'] = 1795435200
    elif fault == 'no-timezone':
        snapshot['generated_at'] = '2026-11-23T12:00:00.123456'
    elif fault == 'backwards-expiry':
        snapshot['expires_at'] = (NOW - timedelta(seconds=1)).isoformat()
    elif fault == 'future-generated':
        snapshot['generated_at'] = (NOW + timedelta(days=1)).isoformat()
        snapshot['expires_at'] = (NOW + timedelta(days=1, seconds=30)).isoformat()
    elif fault == 'missing-running':
        snapshot.pop('running')
    elif fault == 'missing-status':
        snapshot.pop('status')
    elif fault == 'invalid-calendar-date':
        snapshot['generated_at'] = '2026-02-30T12:00:00.123456+00:00'
        snapshot['expires_at'] = '2026-02-30T12:00:30.123456+00:00'
    elif fault == 'missing-schema':
        snapshot.pop('schema')
    elif fault == 'unknown-schema':
        snapshot['schema'] = 2
    elif fault == 'wrong-plugin':
        ui.runtime['id'] = 'different.plugin'
    elif fault == 'false-exists-with-data':
        ui.runtime['exists'] = False
    ui.config['_runtime']['status'] = 'healthy'
    ui.open()
    expect_text(ui.page, '#overview-health', 'unavailable')
    assert 'unavailable' in ui.page.locator('#overview-title').inner_text().lower()
    assert 'LEGACY DATA' not in ui.page.locator('body').inner_text()
    ui.page.locator('[data-tab="settings"]').click()
    expect = pytest.importorskip('playwright.sync_api').expect
    expect(ui.page.locator('#port')).to_have_value('9109')
    ui.page.locator('#port').fill('9111')
    ui.page.locator('button[type="submit"]').click()
    expect(ui.page.locator('#notice')).to_contain_text('Saved.')
    assert ui.posts[-1]['config']['port'] == 9111


@pytest.mark.parametrize('status,running', [('degraded', True), ('starting', True),
                                           ('error', True), ('stopped', False)])
def test_runtime_reports_producer_state_and_stopped_does_not_age_to_stale(plugin_browser, status, running):
    ui = plugin_browser
    ui.runtime['runtime'].update(status=status, running=running)
    ui.open()
    expect_text(ui.page, '#overview-health', status)
    assert status in ui.page.locator('#overview-title').inner_text().lower() or status == 'degraded'
    if status == 'stopped':
        ui.tick(65000)
        expect_text(ui.page, '#overview-health', 'stopped')
        assert 'stale' not in ui.page.locator('#overview-title').inner_text().lower()


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [360, 768, 1440])
def test_theme_navigation_and_save(theme, width):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    config = json.loads((ROOT / 'config.default.json').read_text())
    posts, auth_headers, errors, external = [], [], [], []
    api_paths = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme=theme)
        page.add_init_script("localStorage.setItem('pymc_jwt_token', 'browser-test-jwt')")
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request):
            nonlocal config
            url = urlparse(request.request.url)
            if url.netloc != 'plugin.test' or url.path in ('/metrics', '/healthz'):
                external.append(request.request.url)
                request.abort()
            elif url.path in (SETTINGS_API, RUNTIME_API):
                api_paths.add(url.path)
                auth_headers.append(request.request.headers.get('authorization'))
                if auth_headers[-1] != 'Bearer browser-test-jwt':
                    request.fulfill(status=401)
                    return
                if request.request.method == 'POST':
                    assert url.path == SETTINGS_API
                    payload = request.request.post_data_json
                    posts.append(payload)
                    config = payload['config']
                request.fulfill(json={'config': config} if url.path == SETTINGS_API else runtime_envelope(datetime.now(timezone.utc)))
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
        expect_text(page, '#overview-health', 'healthy')
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
        assert api_paths == {SETTINGS_API, RUNTIME_API}
        page.reload()
        page.locator('[data-tab="settings"]').click()
        page.wait_for_function("document.querySelector('#port').value === '9110'")
        assert not page.locator('#repeater_verify_tls').is_checked()
        folder = os.environ.get('UI_SCREENSHOTS')
        if folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
            page.locator('#label-repeater_api_token').click()
            # Start full-page captures at the top: otherwise a sticky tab bar
            # is composited halfway down the image at the old scroll offset.
            page.evaluate('window.scrollTo(0, 0)')
            page.screenshot(path=f'{folder}/{theme}-{width}-settings.png', full_page=True)
            page.locator('[data-tab="overview"]').click()
            page.evaluate('window.scrollTo(0, 0)')
            page.screenshot(path=f'{folder}/{theme}-{width}-overview.png', full_page=True)
        assert not errors
        assert not external
        browser.close()


@pytest.mark.parametrize('stored_token,expected', [
    (None, 'Authentication is required.'),
    ('expired-test-jwt', 'session has expired.'),
])
@pytest.mark.parametrize('endpoint', [SETTINGS_API, RUNTIME_API])
def test_api_401_explains_dashboard_login(stored_token, expected, endpoint):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        if stored_token:
            page.add_init_script(f"localStorage.setItem('pymc_jwt_token', {json.dumps(stored_token)})")

        def route(request):
            url = urlparse(request.request.url)
            if url.path in (SETTINGS_API, RUNTIME_API):
                assert request.request.headers.get('authorization') == (
                    f'Bearer {stored_token}' if stored_token else None
                )
                if url.path == endpoint:
                    request.fulfill(status=401)
                else:
                    request.fulfill(json={'config': {}} if url.path == SETTINGS_API else runtime_envelope(datetime.now(timezone.utc)))
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


@pytest.mark.parametrize('settings_status', [200, 500])
def test_runtime_recovery_clears_its_error_without_hiding_settings_failure(plugin_browser, settings_status):
    ui = plugin_browser
    ui.statuses.update({SETTINGS_API: settings_status, RUNTIME_API: 503})
    ui.open()
    expect_text(ui.page, '#overview-health', 'unavailable')
    expect = pytest.importorskip('playwright.sync_api').expect
    expect(ui.page.locator('#notice')).to_contain_text('HTTP 503')
    ui.statuses[RUNTIME_API] = 200
    ui.tick()
    expect_text(ui.page, '#overview-health', 'healthy')
    expect(ui.page.locator('#notice')).not_to_contain_text('HTTP 503')
    if settings_status == 500:
        expect(ui.page.locator('#notice')).to_contain_text('HTTP 500')


@pytest.mark.parametrize('status', [404, 500, 503])
def test_runtime_http_failure_discards_old_health_but_does_not_block_settings(plugin_browser, status):
    ui = plugin_browser
    ui.open()
    expect_text(ui.page, '#overview-health', 'healthy')
    ui.statuses[RUNTIME_API] = status
    ui.tick()
    expect_text(ui.page, '#overview-health', 'unavailable')
    assert ui.page.locator('#kpi-metrics').inner_text() == '0'
    if status == 404:
        assert 'compatible' in ui.page.locator('#notice').inner_text().lower()
        assert '/api/plugins/runtime' in ui.page.locator('#notice').inner_text()
        assert '1.1.2' in ui.page.locator('#notice').inner_text()
    else:
        assert f'HTTP {status}' in ui.page.locator('#notice').inner_text()
    ui.page.locator('[data-tab="settings"]').click()
    ui.page.locator('#port').fill('9112')
    ui.page.locator('button[type="submit"]').click()
    pytest.importorskip('playwright.sync_api').expect(ui.page.locator('#notice')).to_contain_text('Saved.')
    assert ui.posts[-1]['config']['port'] == 9112
    ui.statuses[RUNTIME_API] = 200
    ui.tick()
    expect_text(ui.page, '#overview-health', 'healthy')
    assert f'HTTP {status}' not in ui.page.locator('#notice').inner_text()
