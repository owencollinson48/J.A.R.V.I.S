"""Legacy automatic aliases must not rename unrelated applications or prove status."""
import json

import pytest

from jarvis.applications import ApplicationIndex, app_entry
from jarvis.config import Settings
from jarvis.store import Store, now


SPOTIFY=app_entry('Spotify','exe',r'C:\Apps\Spotify.exe','test',r'C:\Apps\Spotify.exe')
CHROME=app_entry('Google Chrome','exe',r'C:\Apps\chrome.exe','test',r'C:\Apps\chrome.exe')
OUTLOOK=app_entry('Outlook (new)','uri','outlookforwindows:','test',r'C:\Apps\olk.exe')
THUNDERBIRD=app_entry('Thunderbird','exe',r'C:\Apps\thunderbird.exe','test',r'C:\Apps\thunderbird.exe')


class Windows:
    supported=True
    def __init__(self,entries):self.entries=entries;self.visible=[];self.focused=[];self.activations=[]
    def discover(self,directories):return self.entries,[]
    def windows(self):return self.visible[:]
    def focus(self,identity):self.focused.append(identity);return True
    def activate(self,launcher):self.activations.append(launcher)


def shown(app,identity=8):
    path=app['executables'][0]
    return {'id':identity,'pid':identity*10,'title':app['name'],'process':path.rsplit('\\',1)[-1],'executable':path}


@pytest.fixture
def setup_index(tmp_path):
    stores=[]
    def create(entries,legacy_aliases=()):
        store=Store(tmp_path/f'index-{len(stores)}.db');stores.append(store)
        # Match the two-column schema shipped before alias provenance existed.
        store.execute('CREATE TABLE application_aliases(alias TEXT PRIMARY KEY,application_id TEXT)')
        for alias,app in legacy_aliases:
            store.execute('INSERT INTO application_aliases VALUES(?,?)',(alias,app['id']))
        settings=Settings(tmp_path/f'settings-{len(stores)}');adapter=Windows(entries)
        index=ApplicationIndex(store,settings,adapter);index.refresh()
        return index,adapter,store,settings
    yield create
    for store in stores:store.db.close()


def test_migrated_spotify_to_chrome_alias_does_not_override_installed_spotify(setup_index):
    index,adapter,store,_=setup_index([SPOTIFY,CHROME],[('spotify',CHROME)])
    assert store.row('SELECT source FROM application_aliases')['source']=='legacy'
    assert index.resolve('Spotify')['id']==SPOTIFY['id']
    adapter.visible=[shown(CHROME)]
    assert index.observe_application('Spotify')['verified'] is False
    assert 'spotify' not in index.search('Chrome')[0]['aliases']
    result=index.launch('Spotify',timeout=0)
    assert result['application_id']==SPOTIFY['id'] and not result['verified']
    assert adapter.focused==[] and adapter.activations==SPOTIFY['launchers']


def test_uninstalled_spotify_stale_alias_is_never_proof_that_chrome_is_spotify(setup_index):
    index,adapter,_,_=setup_index([CHROME],[('spotify',CHROME)])
    adapter.visible=[shown(CHROME)]
    result=index.observe_application('Spotify')
    assert result['verified'] is False and not result.get('application_id')
    with pytest.raises(ValueError,match='not found'):index.launch('Spotify',timeout=0)
    assert adapter.focused==[] and adapter.activations==[]


def test_exact_display_name_outranks_even_explicit_conflicting_alias(setup_index):
    index,adapter,_,_=setup_index([SPOTIFY,CHROME])
    index.alias('Spotify',CHROME['id'])
    assert index.resolve('Spotify')['id']==SPOTIFY['id']
    adapter.visible=[shown(CHROME)]
    assert index.observe_application('Spotify')['verified'] is False


def test_legacy_semantic_email_alias_to_outlook_and_shortened_brand_remain_usable(setup_index):
    index,adapter,_,_=setup_index([OUTLOOK],[('my emails',OUTLOOK),('outlook',OUTLOOK)])
    adapter.visible=[shown(OUTLOOK)]
    for alias in ['my emails','Outlook']:
        result=index.observe_application(alias)
        assert result['verified'] and result['application_id']==OUTLOOK['id']
        assert result['matched_alias']['source']=='legacy'


def test_explicit_generic_user_preference_overrides_builtin_email_default(setup_index):
    index,adapter,store,_=setup_index([OUTLOOK,THUNDERBIRD])
    index.alias('my emails',THUNDERBIRD['id'])
    adapter.visible=[shown(THUNDERBIRD)]
    result=index.observe_application('my emails')
    assert result['verified'] and result['name']=='Thunderbird'
    assert result['matched_alias']=={'alias':'my emails','source':'user'}
    assert store.row('SELECT source FROM application_aliases')['source']=='user'


def test_automatic_launch_alias_is_weak_and_does_not_replace_user_preference(setup_index):
    index,_,store,_=setup_index([OUTLOOK,THUNDERBIRD])
    index.alias('work inbox',THUNDERBIRD['id'])
    index.alias('work inbox',OUTLOOK['id'],source='launch')
    saved=store.row('SELECT * FROM application_aliases')
    assert saved['application_id']==THUNDERBIRD['id'] and saved['source']=='user'
    assert index.resolve('work inbox')['id']==THUNDERBIRD['id']


def test_unrelated_launch_generated_alias_cannot_rename_an_app(setup_index):
    index,adapter,_,_=setup_index([CHROME])
    index.alias('Spotify',CHROME['id'],source='launch')
    adapter.visible=[shown(CHROME)]
    assert index.observe_application('Spotify')['verified'] is False


def test_verified_launch_records_weak_source_without_downgrading_explicit_alias(setup_index):
    index,adapter,store,_=setup_index([THUNDERBIRD])
    index.alias('work inbox',THUNDERBIRD['id'])
    adapter.visible=[shown(THUNDERBIRD)]
    assert index.launch('work inbox',timeout=0)['verified']
    assert store.row("SELECT source FROM application_aliases WHERE alias='work inbox'")['source']=='user'
    assert index.launch('Thunderbird',timeout=0)['verified']
    assert store.row("SELECT source FROM application_aliases WHERE alias='thunderbird'")['source']=='launch'


def test_matching_completed_explicit_alias_tool_restores_user_provenance(setup_index):
    index,adapter,store,settings=setup_index([OUTLOOK,THUNDERBIRD],[('work inbox',THUNDERBIRD)])
    conversation=store.conversation()
    stamp=now()
    args={'alias':'work inbox','application_id':THUNDERBIRD['id']}
    result={**args,'name':'Thunderbird'}
    store.execute('INSERT INTO action_log VALUES(?,?,?,?,?,?,?,?,?,?)',
                  ('saved-alias',None,conversation['id'],'set_application_alias',json.dumps(args),'routine','completed',json.dumps(result),stamp,stamp))
    restarted=ApplicationIndex(store,settings,adapter)
    assert store.row('SELECT source FROM application_aliases')['source']=='user'
    assert restarted.resolve('work inbox')['id']==THUNDERBIRD['id']


@pytest.mark.parametrize('status,target',[('failed',THUNDERBIRD),('completed',CHROME)])
def test_failed_or_different_old_tool_record_cannot_promote_unrelated_alias(setup_index,status,target):
    _,adapter,store,settings=setup_index([CHROME,THUNDERBIRD],[('spotify',CHROME)])
    conversation=store.conversation();stamp=now()
    args={'alias':'spotify','application_id':target['id']}
    store.execute('INSERT INTO action_log VALUES(?,?,?,?,?,?,?,?,?,?)',
                  ('not-proof',None,conversation['id'],'set_application_alias',json.dumps(args),'routine',status,
                   json.dumps({**args,'error':'Alias was not saved'} if status=='failed' else {**args,'application_id':THUNDERBIRD['id']}),stamp,stamp))
    restarted=ApplicationIndex(store,settings,adapter)
    assert store.row('SELECT source FROM application_aliases')['source']=='legacy'
    assert restarted.observe_application('Spotify')['verified'] is False



def test_old_cached_lookup_metadata_is_not_current_alias_evidence(setup_index):
    index,adapter,store,_=setup_index([CHROME])
    polluted={**CHROME,'matched_alias':{'alias':'spotify','source':'user'},'aliases':['spotify'],'score':2}
    store.execute('UPDATE applications SET payload=? WHERE id=?',(json.dumps(polluted),CHROME['id']))
    adapter.visible=[shown(CHROME)]
    observed=index.observe_application('Google Chrome')
    assert observed['verified'] and 'matched_alias' not in observed
    assert index.search('Chrome')[0]['aliases']==[]
    assert 'matched_alias' not in index.resolve(CHROME['id'])
    assert index.observe_application('Spotify')['verified'] is False
