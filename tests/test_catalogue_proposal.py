"""Offline checks for the producer's read-only preparation and narrow publisher."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


release = module('prometheus_release_automation', 'release_automation.py')
publisher = module('prometheus_catalogue_publisher', 'publish_catalogue.py')
SHA = 'a' * 40


def fields(version='1.0.0'):
    return {'version': version, 'source_revision': SHA, 'sha256': 'b' * 64,
            'wheel_url': f'https://github.com/{release.REPOSITORY}/releases/download/v{version}/'
                         f'openhop_prometheus_plugin-{version}-py3-none-any.whl'}


def catalogue():
    return {'schema': 2, 'plugins': [{'id': 'openhop.nomad', 'version': '0.1.2',
            'logo': 'unrelated'}]}


def test_initial_upsert_preserves_others_and_is_not_implicitly_certified():
    base = catalogue()
    result = release.upsert(base, fields())
    assert base == catalogue()
    assert result['plugins'][0] == base['plugins'][0]
    entry = result['plugins'][1]
    assert entry['id'] == release.PLUGIN
    assert entry['category'] == 'integration'
    assert entry['logo'] == (f'https://raw.githubusercontent.com/{release.REPOSITORY}/'
                             f'{SHA}/ui/assets/prometheus-logo.svg')
    logo = ROOT / 'ui/assets/prometheus-logo.svg'
    assert logo.read_bytes().startswith(b'<svg xmlns="http://www.w3.org/2000/svg"')
    assert entry['min_repeater_version'] == '0.3.0'
    assert {key: entry[key] for key in release.FIELDS} == fields()
    assert release.upsert(result, fields()) == result


@pytest.mark.parametrize('release_version', ['1.1.0', '1.1.1', '2.0.0'])
def test_runtime_snapshot_release_initial_minimum(release_version):
    result = release.upsert(catalogue(), fields(release_version))
    assert result['plugins'][1]['min_repeater_version'] == '1.1.2'


@pytest.mark.parametrize('approved_floor,expected', [
    ('0.3.0', '1.1.2'), ('1.1.2', '1.1.2'), ('1.2.0', '1.2.0'),
])
def test_runtime_snapshot_update_raises_but_never_lowers_minimum(approved_floor, expected):
    original = release.upsert(catalogue(), fields())
    original['plugins'][1]['min_repeater_version'] = approved_floor
    preserved = copy.deepcopy(original)
    updated = release.upsert(original, fields('1.1.0'))
    old, new = original['plugins'][1], updated['plugins'][1]
    assert new['min_repeater_version'] == expected
    metadata = release.FIELDS | {'min_repeater_version'}
    assert {k: v for k, v in old.items() if k not in metadata} == {
        k: v for k, v in new.items() if k not in metadata}
    assert updated['plugins'][0] == original['plugins'][0]
    assert original == preserved


def test_runtime_snapshot_update_derives_omitted_optional_minimum():
    original = release.upsert(catalogue(), fields())
    original['plugins'][1].pop('min_repeater_version')
    preserved = copy.deepcopy(original)
    updated = release.upsert(original, fields('1.1.0'))
    assert updated['plugins'][1]['min_repeater_version'] == '1.1.2'
    assert original == preserved


def test_subsequent_update_only_four_fields_and_rejects_rollback_or_mutation():
    original = release.upsert(catalogue(), fields())
    updated = release.upsert(original, fields('1.0.1'))
    old, new = original['plugins'][1], updated['plugins'][1]
    assert {k: v for k, v in old.items() if k not in release.FIELDS} == {
        k: v for k, v in new.items() if k not in release.FIELDS}
    with pytest.raises(ValueError, match='downgrade'):
        release.upsert(updated, fields())
    changed = fields('1.0.1')
    changed['sha256'] = 'c' * 64
    with pytest.raises(ValueError, match='immutable'):
        release.upsert(updated, changed)
    damaged = copy.deepcopy(updated)
    damaged['plugins'][1]['repository'] = 'attacker/repo'
    with pytest.raises(ValueError, match='identity'):
        release.upsert(damaged, fields('1.0.2'))


@pytest.mark.parametrize('omit_minimum', [False, True])
def test_prepare_reports_effective_minimum_and_compatibility_change(tmp_path, monkeypatch, omit_minimum):
    base = release.upsert(catalogue(), fields())
    if omit_minimum:
        base['plugins'][1].pop('min_repeater_version')
    destination = tmp_path / 'catalogue'
    destination.mkdir()
    (destination / 'catalogue.json').write_text(json.dumps(base))
    base_sha = 'd' * 40

    class Policy:
        strict_json = staticmethod(json.loads)

    class API:
        def call(self, path):
            if path == f'/repos/{release.CATALOGUE}/git/ref/heads/main':
                return {'object': {'sha': base_sha}}
            if path.endswith('/git/ref/heads/automation/openhop-prometheus-v1.1.0'):
                raise release.APIError(404)
            raise AssertionError(path)

    def git(root, *args):
        assert root == destination
        if args == ('rev-parse', 'HEAD'):
            return base_sha
        assert args == ('status', '--porcelain', '--untracked-files=no')
        return ''

    monkeypatch.setattr(release, 'load_policy', lambda path: Policy())
    monkeypatch.setattr(release, 'git', git)
    monkeypatch.setattr(release, 'trusted_source', lambda *args: SHA)
    monkeypatch.setattr(release, 'public_release', lambda *args: fields('1.1.0'))
    output = tmp_path / 'proposal'
    receipt = release.prepare(API(), destination, 'v1.1.0', output, run_origin=False)
    candidate = json.loads((output / 'catalogue.json').read_text())
    effective = candidate['plugins'][1]['min_repeater_version']
    assert effective == '1.1.2'
    assert receipt.get('min_repeater_version') == effective
    assert receipt.get('compatibility_changed') is True
    assert receipt['initial'] is False and receipt['changed'] is True
    assert receipt['fields'] == fields('1.1.0')
    assert json.loads((destination / 'catalogue.json').read_text()) == base


def test_duplicate_and_invalid_fields_rejected():
    base = release.upsert(catalogue(), fields())
    base['plugins'].append(copy.deepcopy(base['plugins'][-1]))
    with pytest.raises(ValueError, match='duplicate'):
        release.upsert(base, fields())
    for key, bad in [('source_revision', 'x' * 40), ('sha256', '0'),
                     ('version', '01.0.0'), ('wheel_url', 'https://example.com/local.whl')]:
        invalid = fields()
        invalid[key] = bad
        with pytest.raises(ValueError):
            release.upsert(catalogue(), invalid)
    with pytest.raises(ValueError, match='only four'):
        release.upsert(catalogue(), dict(fields(), extra=True))


def test_release_and_origin_require_tag_bound_success():
    assert release.asset_names('v1.0.0') == (
        'openhop_prometheus_plugin-1.0.0-py3-none-any.whl',
        'openhop-prometheus-plugin-v1.0.0-wheel.zip',
    )
    assert release.release_state({'tag_name': 'v1.0.0', 'draft': False,
             'prerelease': False, 'assets': [{'name': name} for name in release.asset_names('v1.0.0')]},
             'v1.0.0') == 'verify'
    with pytest.raises(ValueError, match='partial'):
        release.release_state({'tag_name': 'v1.0.0', 'draft': False,
             'prerelease': False, 'assets': []}, 'v1.0.0')
    with pytest.raises(ValueError, match='partial'):
        release.release_state({'tag_name': 'v1.0.0', 'draft': False,
             'prerelease': False, 'assets': [{'name': release.asset_names('v1.0.0')[0]}]}, 'v1.0.0')
    run = {'head_branch': 'v1.0.0', 'head_sha': SHA, 'event': 'push',
           'status': 'completed', 'conclusion': 'success',
           'path': '.github/workflows/release.yml', 'name': 'Release plugin wheel',
           'repository': {'full_name': release.REPOSITORY},
           'head_repository': {'full_name': release.REPOSITORY}}
    assert release.valid_origin(run, 'v1.0.0', SHA)
    for modification in ({'head_branch': 'dev'}, {'conclusion': 'failure'},
                         {'head_sha': 'b' * 40}, {'path': '.github/workflows/ci.yml'}):
        assert not release.valid_origin(dict(run, **modification), 'v1.0.0', SHA)
    assert release.event_tag(None, {}, 'workflow_dispatch', 'refs/heads/main', 'v1.0.0') == 'v1.0.0'
    with pytest.raises(ValueError, match='main'):
        release.event_tag(None, {}, 'workflow_dispatch', 'refs/heads/dev', 'v1.0.0')


def test_recovery_origin_must_bind_tag_source_and_main_run():
    tag_sha = SHA
    main_sha = 'c' * 40
    manual = {'id': 34, 'head_branch': 'main', 'head_sha': main_sha,
              'event': 'workflow_dispatch', 'display_title': 'Release plugin wheel v1.0.0',
              'status': 'completed', 'conclusion': 'success',
              'path': '.github/workflows/release.yml', 'name': 'Release plugin wheel v1.0.0',
              'repository': {'full_name': release.REPOSITORY},
              'head_repository': {'full_name': release.REPOSITORY}}
    class API:
        def __init__(self, bad=False):
            self.bad = bad
        def pages(self, path, key=None):
            assert key == 'workflow_runs'
            return [manual] if 'event=workflow_dispatch' in path else []
        def call(self, path):
            if path.endswith('/actions/runs/34'):
                return manual
            if '/compare/' in path:
                left, right = path.split('/compare/')[1].split('...')
                if self.bad or (left, right) not in ((tag_sha, main_sha), (main_sha, 'main')):
                    return {'status': 'diverged', 'merge_base_commit': {'sha': 'd' * 40}}
                return {'status': 'ahead', 'merge_base_commit': {'sha': left}}
            raise AssertionError(path)
    assert release.event_tag(API(), {'workflow_run': manual}, 'workflow_run', '', '') == 'v1.0.0'
    assert release.origin(API(), 'v1.0.0', tag_sha).endswith('/actions/runs/34')
    with pytest.raises(ValueError, match='no successful'):
        release.origin(API(bad=True), 'v1.0.0', tag_sha)
    for mutation in ({'display_title': 'Release plugin wheel v1.0.1'},
                     {'head_branch': 'dev'}, {'conclusion': 'failure'}):
        changed = dict(manual, **mutation)
        assert not release.valid_origin(changed, 'v1.0.0', main_sha)


def test_public_bytes_only_and_missing_registration_fail_closed(monkeypatch):
    class Policy:
        def wheel_url(self, version, plugin):
            assert plugin == release.PLUGIN
            return fields(version)['wheel_url']

        def verify_release(self, api, item):
            assert item['source_revision'] == SHA
            assert item['wheel_url'] == fields()['wheel_url']
            return {'asset_size': 12, 'assets': []}

        def download(self, url):
            assert url == fields()['wheel_url']
            return b'public bytes'

        def verify_wheel(self, raw, item):
            assert raw == b'public bytes'

    result = release.public_release(None, Policy(), 'v1.0.0', SHA,
                                    {'id': release.PLUGIN, 'repository': release.REPOSITORY})
    import hashlib
    assert result['sha256'] == hashlib.sha256(b'public bytes').hexdigest()

    class PrivateRelease(Policy):
        def download(self, url):
            raise OSError('public download unavailable')

    with pytest.raises(OSError, match='public download unavailable'):
        release.public_release(None, PrivateRelease(), 'v1.0.0', SHA,
                               {'id': release.PLUGIN, 'repository': release.REPOSITORY})



def test_publisher_initial_is_draft_and_retry_preserves_review_state():
    receipt = {'changed': True, 'initial': True, 'branch': 'automation/openhop-prometheus-v1.0.0',
               'base_sha': SHA, 'remote_sha': '', 'fields': fields(),
               'min_repeater_version': '0.3.0', 'compatibility_changed': False,
               'origin_run_url': f'https://github.com/{release.REPOSITORY}/actions/runs/1'}
    requests = []
    state = {'number': 7, 'head': {'sha': 'c' * 40, 'ref': receipt['branch'],
             'repo': {'full_name': publisher.REPO}},
             'base': {'ref': 'main', 'repo': {'full_name': publisher.REPO}},
             'state': 'open', 'draft': True}

    def api(path, method='GET', data=None):
        requests.append((path, method, data))
        if path.endswith('/git/ref/heads/main'):
            return {'object': {'sha': SHA}}
        if '/pulls?state=open' in path:
            return []
        if path.endswith('/pulls'):
            state.update({k: v for k, v in data.items() if k not in ('head', 'base')})
            return {'number': 7}
        return state

    publisher.publish(api, lambda r: 'c' * 40, receipt)
    posted = next((path, data) for path, method, data in requests if method == 'POST')
    assert posted[0] == publisher.BASE + '/pulls'
    assert posted[1]['head'] == receipt['branch']
    assert posted[1]['draft'] is True
    assert not any(method == 'PUT' for _, method, _ in requests)
    requests.clear()
    receipt['initial'] = False
    def retry_api(path, method='GET', data=None):
        requests.append((path, method, data))
        if path.endswith('/git/ref/heads/main'):
            return {'object': {'sha': SHA}}
        if '/pulls?state=open' in path:
            return [state]
        if method == 'PATCH':
            state.update(data)
        return state
    publisher.publish(retry_api, lambda r: 'c' * 40, receipt)
    assert state['draft'] is True
    requests.clear()
    publisher.publish(retry_api, lambda r: pytest.fail('no push on no-op'),
                      dict(receipt, changed=False))
    assert not requests
    with pytest.raises(ValueError, match='invalid branch'):
        publisher.push_args('automation/other-v1.0.0', '')
    assert publisher.REPO in publisher.push_args(receipt['branch'], '')[2]
    assert 'yellowcooln/' not in publisher.push_args(receipt['branch'], '')[2]

def test_publisher_rejects_wrong_head_repository():
    receipt = {'changed': True, 'initial': True, 'branch': 'automation/openhop-prometheus-v1.0.0',
               'base_sha': SHA, 'remote_sha': '', 'fields': fields(),
               'min_repeater_version': '0.3.0', 'compatibility_changed': False,
               'origin_run_url': 'https://example.test'}
    bad = {'number': 7, 'head': {'ref': receipt['branch'], 'repo': {'full_name': 'yellowcooln/openhop-plugin-catalogue'}},
           'base': {'ref': 'main', 'repo': {'full_name': publisher.REPO}}, 'state': 'open'}
    def api(path, method='GET', data=None):
        if path.endswith('/git/ref/heads/main'):
            return {'object': {'sha': SHA}}
        if '/pulls?state=open' in path:
            return [bad]
        raise AssertionError('unexpected call or push')
    with pytest.raises(ValueError, match='unexpected PR target'):
        publisher.publish(api, lambda r: pytest.fail('must not push'), receipt)


def test_publisher_describes_effective_minimum_and_metadata_review():
    receipt = {'changed': True, 'initial': False, 'branch': 'automation/openhop-prometheus-v1.1.0',
               'base_sha': SHA, 'remote_sha': '', 'fields': fields('1.1.0'),
               'min_repeater_version': '1.1.2', 'compatibility_changed': True,
               'origin_run_url': f'https://github.com/{release.REPOSITORY}/actions/runs/1'}
    requests = []
    state = {'number': 7, 'head': {'sha': 'c' * 40, 'ref': receipt['branch'],
             'repo': {'full_name': publisher.REPO}},
             'base': {'ref': 'main', 'repo': {'full_name': publisher.REPO}},
             'state': 'open', 'draft': False}

    def api(path, method='GET', data=None):
        requests.append((path, method, data))
        if path.endswith('/git/ref/heads/main'):
            return {'object': {'sha': SHA}}
        if '/pulls?state=open' in path:
            return []
        if method == 'POST':
            assert path == publisher.BASE + '/pulls'
            assert data is not None
            state.update({k: v for k, v in data.items() if k not in ('head', 'base')})
            return {'number': 7}
        return state

    publisher.publish(api, lambda r: 'c' * 40, receipt)
    posted = next(data for _, method, data in requests if method == 'POST')
    assert 'Minimum Repeater version: `1.1.2`' in posted['body']
    assert 'requires maintainer approval' in posted['body']
    assert 'subsequent proposals change only four' not in posted['body']
    assert posted['draft'] is False
    assert not any(method == 'PUT' for _, method, _ in requests)
