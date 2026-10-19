"""Direct control uses the real request engine with local files and fake UI targets."""
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.computer import Computer
from jarvis.config import Settings
from jarvis.execution import Cancelled, PermissionPolicy, RunContext
from jarvis.store import Store


class DirectProvider:
    def __init__(self, settings): self.script, self.seen = [], []
    def headers(self): return {}
    async def check(self): return {'ok': True}
    async def complete(self, messages, tools):
        self.seen.append(messages)
        message = self.script.pop(0) if self.script else {'role': 'assistant', 'content': 'Completed the requested fixture workflow.'}
        yield {'type': 'completion', 'message': message}


def tool_calls(*items):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'direct-' + uuid.uuid4().hex, 'type': 'function',
         'function': {'name': name, 'arguments': json.dumps(arguments)}}
        for index, (name, arguments) in enumerate(items)]}


@pytest.fixture
def direct(tmp_path):
    app = create_app(tmp_path / 'private', provider_factory=DirectProvider)
    root = tmp_path / 'documents'; root.mkdir()
    app.state.settings.update({'roots': [str(root)], 'native_notifications': False})
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/'); client.headers['X-Jarvis-Client'] = 'dashboard'
        client.jarvis, client.root = app, root
        yield client


def test_explicit_request_executes_command_secret_read_and_consequential_fake_ui_without_approval(direct, tmp_path):
    secret = tmp_path / '.aws' / 'credentials'; secret.parent.mkdir(); secret.write_text('synthetic-fixture-value')
    output = tmp_path / 'outside-starting-folder.txt'
    clicked = []

    class FakeDesktop:
        def preflight(self, arguments): return {'target_name': 'Send payment', 'signature': 'observed-local-fixture'}
        def act(self, arguments, preview, context):
            context.check(); clicked.append((arguments, preview))
            return {'verified': True, 'fixture': 'No real application or payment was used'}

    direct.jarvis.state.tools.desktop = FakeDesktop()
    direct.jarvis.state.assistant.provider.script = [tool_calls(
        ('read_file', {'path': str(secret)}),
        ('run_command', {'arguments': [sys.executable, '-c', 'print("fixture command completed")']}),
        ('write_document', {'path': str(output), 'content': 'explicitly requested fixture output'}),
        ('desktop_action', {'action': 'click', 'window_id': 1, 'element_id': 'fixture-payment-button'}))]
    cid = direct.post('/api/conversations').json()['id']
    response = direct.post(f'/api/conversations/{cid}/chat', json={'content':
        'Read the synthetic credential fixture, run my local test command, save the output file, and click Send payment in the fake test window.'})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert not any(event['type'] in {'approval', 'error'} for event in events)
    assert output.read_text() == 'explicitly requested fixture output'
    assert len(clicked) == 1
    assert direct.jarvis.state.store.row('SELECT count(*) AS n FROM approvals')['n'] == 0
    activity = direct.get('/api/activity').json()
    assert activity['workflows'][0]['status'] == 'completed'
    assert len(activity['actions']) == 4
    assert all(action['status'] == 'completed' for action in activity['actions'])
    assert {action['risk'] for action in activity['actions']} >= {'credentials', 'command', 'consequential_ui'}
    assert 'synthetic-fixture-value' not in json.dumps(activity)
    prompt = direct.jarvis.state.assistant.provider.seen[0][0]['content']
    assert 'Execution mode: AUTONOMOUS' in prompt
    assert 'Do not ask for approval' in prompt
    assert 'UNTRUSTED DATA' in prompt and 'Never bypass Windows authentication' in prompt
    assert 'Any absolute path accessible to the current OS account' in prompt


@pytest.mark.parametrize(('name', 'arguments', 'metadata'), [
    ('read_file', {'path': '/home/test/.ssh/id_rsa'}, {}),
    ('write_document', {'path': r'C:\Windows\System32\requested.txt'}, {}),
    ('open_file', {'path': 'requested.ps1'}, {}),
    ('desktop_pointer', {'window_id': 1, 'x': 10, 'y': 20}, {}),
    ('desktop_action', {'action': 'press_keys', 'keys': 'ctrl+shift+delete'}, {'target_name': 'Delete', 'password': True}),
    ('desktop_action', {'action': 'double_click'}, {'target_name': 'Submit purchase'}),
    ('browser_action', {'action': 'navigate', 'url': 'https://example.invalid/checkout'}, {}),
    ('launch_application', {'name': 'Requested Script', 'arguments': ['/requested']}, {}),
])
def test_autonomous_decisions_never_create_review_barriers_for_live_requests(tmp_path, name, arguments, metadata):
    settings = Settings(tmp_path / 'private'); store = Store(tmp_path / 'state.db')
    try:
        conversation = store.conversation('Explicitly requested local task')
        context = RunContext(store, conversation['id'], 'Complete my explicitly requested task')
        assert not PermissionPolicy(settings).decide(name, arguments, metadata, context).confirm
    finally: store.db.close()


def test_autonomous_requires_live_context_and_keeps_cancellation_and_limits(tmp_path):
    settings = Settings(tmp_path / 'private'); store = Store(tmp_path / 'state.db')
    try:
        policy = PermissionPolicy(settings)
        with pytest.raises(ValueError, match='active user request'):
            policy.decide('run_command', {'arguments': ['anything']})
        conversation = store.conversation()
        context = RunContext(store, conversation['id'], 'Run my requested task')
        context.event.set()
        with pytest.raises(Cancelled): policy.decide('run_command', {'arguments': ['anything']}, context=context)
        context = RunContext(store, conversation['id'], 'Run my requested task')
        context.actions = 80
        with pytest.raises(Cancelled, match='action limit'):
            context.log('run_command', {'arguments': ['anything']}, policy.decide('run_command', {'arguments': ['anything']}, context=context))
        context = RunContext(store, conversation['id'], 'Run my requested task')
        context.finish('completed')
        with pytest.raises(Cancelled, match='already ended'):
            policy.decide('run_command', {'arguments': ['anything']}, context=context)
    finally: store.db.close()


def test_legacy_settings_migrate_once_then_preserve_user_choices_and_credentials(tmp_path):
    private = tmp_path / 'private'; private.mkdir()
    saved = {'permissions_mode': 'review', 'roots': [str(tmp_path)], 'model': 'my-model',
             'api_key': 'synthetic-existing-key', 'name': 'Owen'}
    (private / 'settings.json').write_text(json.dumps(saved))
    settings = Settings(private)
    assert settings.values['permissions_mode'] == 'autonomous'
    assert settings.values['computer_access'] == 'full'
    assert settings.api_key == 'synthetic-existing-key' and settings.values['model'] == 'my-model'
    assert 'access_policy_version' not in settings.public()
    settings.update({'permissions_mode': 'routine', 'computer_access': 'folders'})
    reopened = Settings(private)
    assert reopened.values['permissions_mode'] == 'routine'
    assert reopened.values['computer_access'] == 'folders'
    assert reopened.api_key == 'synthetic-existing-key'
    with pytest.raises(ValueError, match='Unknown settings field'):
        reopened.update({'access_policy_version': 0})


def test_full_access_resolves_other_locations_symlinks_and_private_app_state(tmp_path):
    settings = Settings(tmp_path / 'private')
    starting = tmp_path / 'starting'; starting.mkdir()
    settings.update({'roots': [str(starting)]})
    outside = tmp_path / 'other-volume'; outside.mkdir()
    document = outside / 'requested.txt'; document.write_text('Other location')
    computer = Computer(settings)
    assert computer.read_file(str(document))['content'] == 'Other location'
    assert computer.path(str(settings.path)) == settings.path
    assert computer.path('../other-volume/requested.txt') == document
    link = starting / 'other-location.txt'
    try: link.symlink_to(document)
    except OSError: pytest.skip('Symlinks unavailable on this host')
    assert computer.path(str(link)) == document
    settings.update({'computer_access': 'folders'})
    with pytest.raises(ValueError, match='outside computer access folders'): computer.path(str(document))
    with pytest.raises(ValueError, match='outside computer access folders'): computer.path(str(link))
    # Full access changes the app scope, never the OS account or filesystem rights.
    settings.update({'computer_access': 'full'})
    assert computer.path(str(document)) == document


def test_full_search_includes_attached_locations_and_hidden_system_folders(tmp_path, monkeypatch):
    settings = Settings(tmp_path / 'private')
    starting = tmp_path / 'starting'; starting.mkdir()
    settings.update({'roots': [str(starting)]})
    volumes = [tmp_path / 'volume-c', tmp_path / 'volume-d']
    files = []
    for index, volume in enumerate(volumes):
        hidden = volume / ('AppData' if index else '.hidden') / 'Windows'
        hidden.mkdir(parents=True)
        target = hidden / f'needle-{index}.txt'; target.write_text('fixture'); files.append(target)
    computer = Computer(settings)
    monkeypatch.setattr(computer, 'drives', lambda: [str(volume) for volume in volumes])
    result = computer.search_files('needle')
    assert {item['path'] for item in result['results']} == {str(item) for item in files}
    assert result['limited'] is False and result['files_scanned'] == 2
    assert result['locations'] == [str(starting), *(str(volume) for volume in volumes)]


def test_full_access_preserves_file_fingerprint_and_backup_guards(tmp_path):
    settings = Settings(tmp_path / 'private'); computer = Computer(settings)
    outside = tmp_path / 'document.txt'; outside.write_text('Original')
    preview = computer.preview('write_document', {'path': str(outside), 'content': 'Replacement'})
    outside.write_text('Changed by another application')
    with pytest.raises(ValueError, match='File changed'):
        computer.write_document(str(outside), 'Replacement', preview['fingerprint'])
    assert outside.read_text() == 'Changed by another application'
    result = computer.write_document(str(outside), 'Requested replacement', computer.fingerprint(outside))
    assert Path(result['backup']).read_text() == 'Changed by another application'
    assert outside.read_text() == 'Requested replacement'


def test_optional_routine_mode_still_reviews_consequential_double_click(tmp_path):
    settings = Settings(tmp_path / 'private'); settings.update({'permissions_mode': 'routine'})
    store = Store(tmp_path / 'state.db')
    try:
        context = RunContext(store, store.conversation()['id'], 'Use my requested app')
        decision = PermissionPolicy(settings).decide('desktop_action', {'action': 'double_click'}, {'target_name': 'Submit purchase'}, context)
        assert decision.confirm and decision.risk == 'consequential_ui'
    finally: store.db.close()


def _events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def _stage_pending_and_deferred(client, pending_path, deferred_path):
    assert client.patch('/api/settings', json={'permissions_mode': 'review'}).status_code == 200
    cid = client.post('/api/conversations').json()['id']
    client.jarvis.state.assistant.provider.script = [tool_calls(
        ('write_document', {'path': str(pending_path), 'content': 'Old queued effect'}),
        ('write_document', {'path': str(deferred_path), 'content': 'Dependent queued effect'}))]
    events = _events(client.post(f'/api/conversations/{cid}/chat', json={'content': 'Prepare my old fixture documents'}))
    assert any(event['type'] == 'approval' for event in events)
    rows = client.jarvis.state.store.rows('SELECT * FROM approvals WHERE conversation_id=? ORDER BY created_at', (cid,))
    assert {row['status'] for row in rows} == {'pending', 'deferred'}
    assert not pending_path.exists() and not deferred_path.exists()
    return cid, {row['tool_call_id'] for row in rows}


def _assert_retired_without_effects(client, cid, call_ids, pending_path, deferred_path):
    assert not pending_path.exists() and not deferred_path.exists()
    history = client.get(f'/api/conversations/{cid}').json()
    assert history['approvals'] == []
    assert history['workflow']['status'] == 'cancelled'
    assert all(action['status'] == 'cancelled' for action in history['actions'])
    rows = client.jarvis.state.store.rows('SELECT status FROM approvals WHERE conversation_id=?', (cid,))
    assert all(row['status'] == 'cancelled' for row in rows)
    answers = [message for message in client.jarvis.state.store.model_history(cid)
               if message['role'] == 'tool' and message['tool_call_id'] in call_ids]
    assert {message['tool_call_id'] for message in answers} == call_ids
    assert len(answers) == len(call_ids)
    assert all(json.loads(message['content'])['cancelled'] for message in answers)


def test_switching_to_autonomous_retires_old_reviews_and_allows_fresh_request(direct, tmp_path):
    pending = direct.root / 'old-pending.txt'; deferred = direct.root / 'old-deferred.txt'
    cid, calls = _stage_pending_and_deferred(direct, pending, deferred)
    response = direct.patch('/api/settings', json={'permissions_mode': 'autonomous', 'computer_access': 'full'})
    assert response.status_code == 200, response.text
    _assert_retired_without_effects(direct, cid, calls, pending, deferred)
    requested = tmp_path / 'new-location.txt'
    direct.jarvis.state.assistant.provider.script = [tool_calls(
        ('write_document', {'path': str(requested), 'content': 'Fresh user request'}))]
    events = _events(direct.post(f'/api/conversations/{cid}/chat', json={'content': 'Create my new document in the other location'}))
    assert not any(event['type'] in {'approval', 'error'} for event in events)
    assert requested.read_text() == 'Fresh user request'
    assert not pending.exists() and not deferred.exists()


def test_restart_migrates_legacy_settings_and_retires_persisted_review_barriers(tmp_path):
    private = tmp_path / 'private'; documents = tmp_path / 'documents'; documents.mkdir()
    app = create_app(private, provider_factory=DirectProvider)
    app.state.settings.update({'roots': [str(documents)], 'native_notifications': False})
    pending = documents / 'restart-pending.txt'; deferred = documents / 'restart-deferred.txt'
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/'); client.headers['X-Jarvis-Client'] = 'dashboard'; client.jarvis = app
        cid, calls = _stage_pending_and_deferred(client, pending, deferred)
    # The real shutdown cancels pending tasks. Restore the persisted old-version
    # awaiting-review state to represent an app exit/crash before that cleanup.
    store = Store(private / 'jarvis.db')
    store.execute('DELETE FROM messages WHERE conversation_id=? AND role=?', (cid, 'tool'))
    approvals = store.rows('SELECT id FROM approvals WHERE conversation_id=? ORDER BY created_at', (cid,))
    store.execute("UPDATE approvals SET status='pending' WHERE id=?", (approvals[0]['id'],))
    store.execute("UPDATE approvals SET status='deferred' WHERE id=?", (approvals[1]['id'],))
    store.execute("UPDATE workflows SET status='awaiting_confirmation' WHERE conversation_id=?", (cid,))
    store.execute("UPDATE action_log SET status='awaiting_confirmation' WHERE conversation_id=?", (cid,))
    store.db.close()
    saved_path = private / 'settings.json'
    saved = json.loads(saved_path.read_text())
    saved.pop('access_policy_version'); saved.pop('computer_access')
    saved_path.write_text(json.dumps(saved))
    restarted = create_app(private, provider_factory=DirectProvider)
    with TestClient(restarted, base_url='http://127.0.0.1') as client:
        client.get('/'); client.headers['X-Jarvis-Client'] = 'dashboard'; client.jarvis = restarted
        settings = client.get('/api/settings').json()
        assert settings['permissions_mode'] == 'autonomous' and settings['computer_access'] == 'full'
        _assert_retired_without_effects(client, cid, calls, pending, deferred)
        requested = tmp_path / 'outside-original-roots.txt'
        restarted.state.assistant.provider.script = [tool_calls(
            ('write_document', {'path': str(requested), 'content': 'Fresh post-upgrade request'}))]
        events = _events(client.post(f'/api/conversations/{cid}/chat', json={'content': 'Save my new document at the requested location'}))
        assert not any(event['type'] in {'approval', 'error'} for event in events)
        assert requested.read_text() == 'Fresh post-upgrade request'
        assert not pending.exists() and not deferred.exists()


def test_migration_loads_saved_windows_drive_roots_without_host_path_validation(tmp_path):
    private = tmp_path / 'private'; private.mkdir()
    roots = [r'C:\Users\owenc', 'D:\\', r'\\server\documents']
    (private / 'settings.json').write_text(json.dumps({'roots': roots, 'permissions_mode': 'routine'}))
    settings = Settings(private)
    assert settings.values['roots'] == roots
    assert settings.values['permissions_mode'] == 'autonomous'
    assert settings.values['computer_access'] == 'full'
    assert json.loads(settings.path.read_text())['roots'] == roots


def test_full_mode_saves_unrelated_settings_with_offline_remembered_directories(tmp_path):
    settings = Settings(tmp_path / 'private')
    roots = [str(tmp_path / 'unplugged-drive' / 'documents'), r'Z:\Remembered network folder']
    apps = [str(tmp_path / 'offline-app-library')]
    settings.update({'roots': roots, 'application_directories': apps})
    public = settings.update({'model': 'new-model', 'api_key': 'synthetic-fixture-key'})
    assert public['model'] == 'new-model' and public['has_api_key']
    assert settings.values['roots'] == roots
    assert settings.values['application_directories'] == apps
    reopened = Settings(settings.data_dir)
    assert reopened.values['roots'] == roots and reopened.values['application_directories'] == apps
    assert reopened.api_key == 'synthetic-fixture-key'
    with pytest.raises((ValueError, OSError)):
        reopened.update({'computer_access': 'folders'})
    assert reopened.values['computer_access'] == 'full'
    existing = tmp_path / 'connected-documents'; existing.mkdir()
    with pytest.raises((ValueError, OSError)):
        reopened.update({'computer_access': 'folders', 'roots': [str(existing)]})
    reopened.update({'computer_access': 'folders', 'roots': [str(existing)], 'application_directories': []})
    assert reopened.values['computer_access'] == 'folders'


@pytest.mark.parametrize('field', ['roots', 'application_directories'])
@pytest.mark.parametrize('values', [[None], [17], [{}], [''], ['bad\x00path']])
def test_full_mode_still_validates_remembered_path_value_types(tmp_path, field, values):
    settings = Settings(tmp_path / 'private')
    with pytest.raises(ValueError, match='nonempty folder paths'):
        settings.update({field: values})


def test_missing_first_root_falls_back_for_relative_files_and_default_command_cwd(tmp_path, monkeypatch):
    home = tmp_path / 'current-user'; home.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    settings = Settings(tmp_path / 'private')
    remembered = tmp_path / 'unplugged-drive' / 'documents'
    settings.update({'roots': [str(remembered)]})
    computer = Computer(settings)
    assert computer.default_folder() == home
    assert computer.path('requested.txt') == home / 'requested.txt'
    preview = computer.preview('write_document', {'path': 'requested.txt', 'content': 'My requested file'})
    result = computer.write_document('requested.txt', 'My requested file', preview['fingerprint'])
    assert Path(result['written']) == home / 'requested.txt'
    assert computer.list_files()['path'] == str(home)
    computer.preview('run_command', {'arguments': [sys.executable, '-c', 'print("ready")']})
    command = computer.run_command([sys.executable, '-c', 'import os;print(os.getcwd())'])
    assert command['exit_code'] == 0 and command['stdout'].strip() == str(home)
    assert settings.values['roots'] == [str(remembered)]
    assert json.loads(settings.path.read_text())['roots'] == [str(remembered)]
