r"""Opt-in native checks. Run only on an interactive Windows desktop.

set JARVIS_WINDOWS_LIVE=1
.venv\Scripts\python.exe -m pytest tests/test_windows_live.py -v -s

These checks open installed applications. They never close an existing window or
force-kill an app. Close any windows left by the check yourself.
"""
import os
import platform

import pytest

from jarvis.applications import ApplicationIndex
from jarvis.config import Settings
from jarvis.store import Store

pytestmark = pytest.mark.skipif(platform.system() != 'Windows' or os.getenv('JARVIS_WINDOWS_LIVE') != '1',
                                reason='Requires opted-in interactive Windows desktop')


@pytest.fixture
def native_index(tmp_path):
    store = Store(tmp_path / 'native.db')
    index = ApplicationIndex(store, Settings(tmp_path / 'settings'))
    report = index.refresh(force=True)
    print('Discovery:',report)
    assert report['count'] > 0
    yield index
    store.db.close()


@pytest.mark.parametrize('query,kind', [('Notepad',None),('Calculator','aumid'),('Outlook',None)])
def test_installed_application_visible_launch(native_index,query,kind):
    candidates = native_index.search(query)
    if kind: candidates = [app for app in candidates if any(method['kind']==kind for method in app['launchers'])]
    if not candidates: pytest.skip(f'{query} with requested activation type is not installed')
    # Test each installed Outlook edition separately; no arbitrary choice between editions.
    for app in candidates[:4] if query=='Outlook' else candidates[:1]:
        result = native_index.launch(app['id'])
        print(app['name'],result)
        assert result['verified'], result
        assert result.get('window_id'), result
