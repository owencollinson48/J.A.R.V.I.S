"""Application identity and current-window checks; no real applications launch."""
import json

import pytest

from jarvis.applications import ApplicationIndex, app_entry
from jarvis.config import Settings
from jarvis.store import Store


SPOTIFY = r'C:\Users\Owner\AppData\Roaming\Spotify\Spotify.exe'
CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'


def window(identity=8, *, pid=80, process='Spotify.exe', executable=SPOTIFY, title='Artist — Current track', **extra):
    return {'id':identity,'pid':pid,'process':process,'executable':executable,'title':title,**extra}


class Windows:
    supported = True
    def __init__(self, entry, visible):
        self.entry, self.visible = entry, list(visible)
        self.activations, self.focused, self.discoveries, self.scans = [], [], 0, 0
        self.foreground = 0
        self.focus_allowed = True
        self.after_focus = None
        self.on_activate = lambda launcher:None
    def discover(self,folders):
        self.discoveries += 1
        return [self.entry], []
    def windows(self):
        self.scans += 1
        return [dict(item) for item in self.visible]
    def foreground_window(self): return self.foreground
    def focus(self,identity):
        self.focused.append(identity)
        if self.focus_allowed: self.foreground = identity
        if self.after_focus: self.after_focus()
        return self.focus_allowed
    def activate(self,launcher):
        self.activations.append(launcher)
        return self.on_activate(launcher)


@pytest.fixture
def index_for(tmp_path):
    stores = []
    def create(entry=None, visible=()):
        entry = entry or app_entry('Spotify','uri','spotify:','protocol',SPOTIFY)
        store = Store(tmp_path/f'index-{len(stores)}.db')
        stores.append(store)
        adapter = Windows(entry,visible)
        index = ApplicationIndex(store,Settings(tmp_path/f'settings-{len(stores)}'),adapter)
        index.refresh()
        return index,adapter,store
    yield create
    for store in stores: store.db.close()


@pytest.mark.parametrize('title',['Spotify','Open Spotify','JARVIS — Spotify','Spotify - Google Chrome'])
def test_browser_request_or_page_title_is_not_spotify(index_for,title):
    index,adapter,store = index_for(visible=[window(process='chrome.exe',executable=CHROME,title=title)])
    result = index.launch('Spotify',timeout=0)
    assert result['verified'] is False and result['observed_running'] is False
    assert adapter.focused == [] and len(adapter.activations)==1
    assert store.row('SELECT count(*) AS n FROM application_launches WHERE verified=1')['n']==0


def test_spotify_label_on_another_application_is_not_identity(index_for):
    entry = app_entry('Spotify','uri','spotify:','protocol')
    index,adapter,_ = index_for(entry,[window(process='notepad.exe',executable=r'C:\Windows\notepad.exe',title='Spotify')])
    assert index.launch('Spotify',timeout=0)['verified'] is False
    assert adapter.focused == []


def test_web_shortcut_shared_chrome_executable_does_not_match_every_browser_window(index_for):
    entry = app_entry('Spotify','shortcut',r'C:\Start Menu\Spotify.lnk','Start Menu',CHROME)
    entry['launchers'][0].update(executable=CHROME,has_arguments=True)
    index,adapter,_ = index_for(entry,[window(process='chrome.exe',executable=CHROME,title='Spotify')])
    def activate(launcher):
        adapter.visible.append(window(9,pid=90,process='chrome.exe',executable=CHROME,title='Spotify'))
        return 90
    adapter.on_activate = activate
    assert index.launch('Spotify',timeout=0)['verified'] is False
    assert adapter.focused == []


def test_new_visible_window_with_launch_pid_but_wrong_app_is_not_success(index_for):
    index,adapter,_ = index_for()
    def activate(launcher):
        adapter.visible.append(window(9,pid=123,process='chrome.exe',executable=CHROME,title='Spotify'))
        return 123
    adapter.on_activate = activate
    assert index.launch('Spotify',timeout=0)['verified'] is False
    assert adapter.focused == []


def test_known_different_executable_path_is_not_accepted_by_basename(index_for):
    index,adapter,_ = index_for(visible=[window(executable=r'C:\Other App\Spotify.exe',title='Spotify')])
    assert index.launch('Spotify',timeout=0)['verified'] is False
    assert adapter.focused == []


def test_real_spotify_path_with_track_title_is_focused_and_rechecked(index_for):
    index,adapter,store = index_for(visible=[window(executable=SPOTIFY.upper().replace('\\','/'))])
    result = index.launch('Spotify',timeout=0)
    assert result['verified'] and result['focused'] and result['already_running']
    assert result['window']['pid']==80 and result['evidence']['matched_by']=='executable_path'
    assert adapter.activations == [] and adapter.focused == [8] and adapter.scans==2
    assert store.row('SELECT verified FROM application_launches')['verified']==1


def test_new_real_app_is_verified_only_after_visible_identity_and_focus(index_for):
    index,adapter,store = index_for()
    adapter.on_activate = lambda launcher:adapter.visible.append(window())
    result = index.launch('Spotify',timeout=0)
    assert result['verified'] and result['focused'] and not result['already_running']
    assert len(adapter.activations)==1 and adapter.scans==3
    saved = json.loads(store.row('SELECT payload FROM applications')['payload'])
    assert saved['preferred']['kind']=='uri'


@pytest.mark.parametrize('change',['focus_denied','foreground_stolen','disappeared','pid_reused'])
def test_failed_focus_or_changed_app_is_not_recorded_as_verified(index_for,change):
    index,adapter,store = index_for(visible=[window()])
    if change=='focus_denied': adapter.focus_allowed=False
    elif change=='foreground_stolen': adapter.after_focus=lambda:setattr(adapter,'foreground',999)
    elif change=='disappeared': adapter.after_focus=adapter.visible.clear
    else: adapter.after_focus=lambda:adapter.visible[0].update(pid=999)
    result = index.launch('Spotify',timeout=0)
    assert result['verified'] is False
    assert store.row('SELECT verified FROM application_launches')['verified']==0
    assert store.row('SELECT count(*) AS n FROM application_aliases')['n']==0
    assert 'preferred' not in json.loads(store.row('SELECT payload FROM applications')['payload'])
    assert adapter.activations == []


def test_read_only_current_observation_detects_closed_app_without_actions_or_discovery(index_for):
    index,adapter,_ = index_for(visible=[window()])
    launched = index.launch('Spotify',timeout=0)
    adapter.visible.clear()
    assert index.observe_launch(launched)['verified'] is False
    assert index.observe_application('Spotify')['verified'] is False
    assert adapter.discoveries==1 and adapter.activations==[] and adapter.focused==[8]


def test_read_only_observation_preserves_running_app_when_it_is_in_background(index_for):
    index,adapter,_ = index_for(visible=[window()])
    result = index.observe_application('Spotify')
    assert result['verified'] and result['observed_running'] and result['focused'] is False
    assert adapter.focused==[] and adapter.activations==[] and adapter.discoveries==1


def test_old_window_handle_reused_by_another_pid_fails_fresh_launch_observation(index_for):
    index,adapter,_ = index_for(visible=[window()])
    launched = index.launch('Spotify',timeout=0)
    adapter.visible[0]['pid']=999
    assert index.observe_launch(launched)['verified'] is False


def test_packaged_aumid_and_hosted_child_process_identity_are_supported(index_for):
    aumid='SpotifyAB.SpotifyMusic_test!Spotify'
    entry=app_entry('Spotify','aumid',aumid,'StartApps')
    hosted=window(process='ApplicationFrameHost.exe',executable=r'C:\Windows\System32\ApplicationFrameHost.exe',
                  title='A current song',app_processes=[{'pid':99,'process':'Spotify.exe','aumid':aumid}])
    index,adapter,_ = index_for(entry,[hosted])
    result=index.launch('Spotify',timeout=0)
    assert result['verified'] and result['evidence']['matched_by']=='aumid'
    assert adapter.activations==[]


def test_different_explicit_packaged_identity_overrides_shared_executable(index_for):
    entry=app_entry('Spotify','aumid','SpotifyFamily!Spotify','package',SPOTIFY)
    index,adapter,_=index_for(entry,[window(aumid='SpotifyFamily!OtherApp')])
    assert index.launch('Spotify',timeout=0)['verified'] is False
    assert adapter.focused==[]


def test_packaged_calculator_family_with_exact_title_remains_supported(index_for):
    entry=app_entry('Calculator','aumid','CalculatorFamily!App','StartApps')
    visible=window(process='ApplicationFrameHost.exe',executable=r'C:\Windows\System32\ApplicationFrameHost.exe',
                   title='Calculator',package_family='CalculatorFamily')
    index,_,_=index_for(entry,[visible])
    assert index.launch('Calculator',timeout=0)['verified']


def test_launch_does_not_replace_spotify_with_a_weak_fuzzy_match(index_for):
    entry=app_entry('Spotlight','exe',r'C:\Apps\Spotlight.exe','App Paths',r'C:\Apps\Spotlight.exe')
    index,adapter,_=index_for(entry)
    result=index.launch('Spotify',timeout=0)
    assert result['verified'] is False and result['ambiguous']
    assert adapter.activations==[] and adapter.focused==[]


def test_current_browser_app_itself_can_still_be_focused(index_for):
    entry=app_entry('Google Chrome','exe',CHROME,'App Paths',CHROME)
    index,_,_=index_for(entry,[window(process='chrome.exe',executable=CHROME,title='Some website')])
    assert index.launch('Google Chrome',timeout=0)['verified']


def test_fresh_index_miss_and_failed_activation_do_not_repeat_slow_discovery(index_for):
    index,adapter,_=index_for()
    with pytest.raises(ValueError,match='not found'):index.launch('zzzz unrelated application',timeout=0)
    assert adapter.discoveries==1
    def failed(launcher): raise OSError('Registered executable missing')
    adapter.on_activate=failed
    result=index.launch('Spotify',timeout=0)
    assert not result['verified'] and result['refresh_recommended']
    assert adapter.discoveries==1


def test_packaged_process_native_api_preserves_64_bit_handles_and_releases_them(monkeypatch):
    import ctypes
    import types
    from ctypes import wintypes
    from jarvis.applications import WindowsApplications
    handle=(1<<45)+123
    closed=[]
    def open_process(access,inherit,pid):
        assert access==0x1000 and inherit is False and pid==88
        return handle
    def close(value):closed.append(value);return 1
    def query(text):
        def call(value,length,buffer):
            assert value==handle
            if buffer is None:length._obj.value=len(text)+1;return 122
            buffer.value=text
            return 0
        return call
    dll=types.SimpleNamespace(OpenProcess=open_process,CloseHandle=close,
                             GetApplicationUserModelId=query('PackageFamily!App'),GetPackageFamilyName=query('PackageFamily'))
    monkeypatch.setattr(ctypes,'WinDLL',lambda name,**kwargs:dll,raising=False)
    assert WindowsApplications.process_identity(88)=={'aumid':'PackageFamily!App','package_family':'PackageFamily'}
    assert dll.OpenProcess.restype is wintypes.HANDLE
    assert dll.CloseHandle.argtypes==[wintypes.HANDLE]
    assert closed==[handle]


def test_packaged_process_query_failure_releases_owned_handle(monkeypatch):
    import ctypes
    import types
    from jarvis.applications import WindowsApplications
    closed=[]
    def open_process(*args):return 987
    def close(value):closed.append(value)
    def query(*args):raise OSError('Query unavailable')
    dll=types.SimpleNamespace(OpenProcess=open_process,CloseHandle=close,GetApplicationUserModelId=query)
    monkeypatch.setattr(ctypes,'WinDLL',lambda name,**kwargs:dll,raising=False)
    with pytest.raises(OSError):WindowsApplications.process_identity(88)
    assert closed==[987]


def test_native_window_enumeration_caches_processes_reads_uwp_children_and_skips_cloaked(monkeypatch):
    import ctypes
    import types
    import psutil
    from jarvis.applications import WindowsApplications
    pids={100:1,101:11,102:33,200:11}
    names={1:'ApplicationFrameHost.exe',11:'Spotify.exe',33:'Spotify.exe'}
    process_calls=[]
    def process(pid):
        process_calls.append(pid)
        return types.SimpleNamespace(exe=lambda:'C:/Apps/'+names[pid],name=lambda:names[pid])
    def thread_process(hwnd,pointer):pointer._obj.value=pids[hwnd];return 1
    def text_length(hwnd):return len('Spotify' if hwnd!=100 else 'Hosted app')
    def text(hwnd,buffer,length):buffer.value='Spotify' if hwnd!=100 else 'Hosted app';return length-1
    def enum_windows(callback,data):
        for identity in [100,101,102]:callback(identity,data)
        return 1
    def enum_children(hwnd,callback,data):
        assert hwnd==100
        callback(200,data)
        return 1
    def cloaked(hwnd,attribute,pointer,size):
        assert attribute==14
        pointer._obj.value=int(hwnd==102)
        return 0
    user32=types.SimpleNamespace(GetWindowThreadProcessId=thread_process,GetWindowTextLengthW=text_length,
                                GetWindowTextW=text,IsWindowVisible=lambda hwnd:True,
                                EnumWindows=enum_windows,EnumChildWindows=enum_children)
    dwmapi=types.SimpleNamespace(DwmGetWindowAttribute=cloaked)
    monkeypatch.setattr(ctypes,'WinDLL',lambda name,**kwargs:dwmapi if name=='dwmapi' else user32,raising=False)
    monkeypatch.setattr(ctypes,'WINFUNCTYPE',ctypes.CFUNCTYPE,raising=False)
    monkeypatch.setattr('jarvis.applications.platform.system',lambda:'Windows')
    monkeypatch.setattr(psutil,'Process',process)
    identities=[]
    def identity(pid):
        identities.append(pid)
        return {'aumid':'SpotifyFamily!App'} if pid==11 else {}
    monkeypatch.setattr(WindowsApplications,'process_identity',identity)
    windows=WindowsApplications.windows()
    assert [item['id'] for item in windows]==[100,101]
    assert process_calls==[1,11] and identities==[1,11]
    assert windows[0]['app_processes'][0]['aumid']=='SpotifyFamily!App'
    assert windows[1]['aumid']=='SpotifyFamily!App'


@pytest.fixture
def clock(monkeypatch):
    """Advance only the application's wait clock; never wait for a real app."""
    import types
    import time
    class Clock:
        elapsed = 0
        on_wait = None
        def monotonic(self): return self.elapsed
        def sleep(self,seconds):
            self.elapsed += seconds
            if self.on_wait: self.on_wait()
    clock = Clock()
    monkeypatch.setattr('jarvis.applications.time',types.SimpleNamespace(time=time.time,monotonic=clock.monotonic,sleep=clock.sleep))
    return clock


def native_state(adapter):
    def state(identity):
        shown = next((item for item in adapter.visible if item['id']==identity),None)
        if not shown: raise ValueError('Window no longer exists')
        return {'focused':adapter.foreground==identity,'minimized':shown.get('minimized',False),
                'maximized':False,'fullscreen':False,'bounds':[0,0,1000,700]}
    adapter.window_state = state


def prior_launch(index,shown):
    return {'application_id':index.resolve('Spotify')['id'],'window_id':shown['id'],
            'window':dict(shown),'verified':True,'focused':True}


def test_current_canonical_app_can_replace_its_window_and_process_without_title_dependency(index_for):
    index,adapter,_ = index_for(visible=[window(9,pid=99,title='A different artist and document')])
    result = index.observe_launch(prior_launch(index,window()),timeout=0)
    assert result['verified'] and result['observed_running']
    assert result['window_id']==9 and result['window']['pid']==99 and result['window_replaced']
    assert adapter.activations==[] and adapter.focused==[] and adapter.discoveries==1


def test_replacement_browser_window_with_the_same_title_is_not_the_application(index_for):
    index,adapter,_ = index_for(visible=[window(9,pid=99,process='chrome.exe',executable=CHROME,title='Spotify')])
    result = index.observe_launch(prior_launch(index,window()),timeout=0,recover=True)
    assert not result['verified'] and not result['observed_running']
    assert adapter.focused==[] and adapter.activations==[]


def test_launch_observation_waits_for_splash_to_main_window_handover_without_relaunch(index_for,clock):
    index,adapter,_ = index_for(visible=[window(9,pid=99,class_name='SpotifySplashScreen')])
    clock.on_wait = lambda: setattr(adapter,'visible',[window(10,pid=100,title='Now playing')]) if clock.elapsed>=.2 else None
    result = index.observe_launch(prior_launch(index,window()),timeout=.6)
    assert result['verified'] and result['window_id']==10 and result['window_replaced']
    assert .2<=clock.elapsed<=.6
    assert adapter.focused==[] and adapter.activations==[]


def test_reused_old_handle_does_not_prevent_finding_an_independent_canonical_replacement(index_for):
    index,adapter,_ = index_for(visible=[window(pid=999),window(10,pid=100)])
    result = index.observe_launch(prior_launch(index,window()),timeout=0)
    assert result['verified'] and result['window_id']==10 and result['window']['pid']==100
    assert adapter.focused==[]


def test_read_only_minimized_observation_and_requested_restore_are_distinct(index_for):
    index,adapter,store = index_for(visible=[window(minimized=True)])
    native_state(adapter)
    launched = prior_launch(index,window())
    observed = index.observe_launch(launched,timeout=0)
    assert observed['verified'] and observed['minimized'] and observed['focused'] is False
    assert 'recovery_attempted' not in observed and 'recovery_failed' not in observed
    assert adapter.focused==[]
    adapter.after_focus = lambda:adapter.visible[0].update(minimized=False)
    recovered = index.observe_launch(launched,timeout=0,recover=True)
    assert recovered['verified'] and recovered['focused'] and not recovered['minimized']
    assert recovered['recovery_attempted'] is True and recovered['recovery_failed'] is False
    assert recovered['bounds']==[0,0,1000,700]
    assert adapter.focused==[8] and adapter.activations==[]
    assert store.row('SELECT count(*) AS n FROM application_launches')['n']==0


@pytest.mark.parametrize('minimized',[False,True])
def test_restore_cannot_claim_focus_or_completion_when_windows_denies_it(index_for,minimized):
    index,adapter,_ = index_for(visible=[window(minimized=minimized)])
    native_state(adapter)
    adapter.focus_allowed = False
    observed = index.observe_launch(prior_launch(index,window()),timeout=0,recover=True)
    assert observed['observed_running'] and observed['minimized'] is minimized and observed['focused'] is False
    assert observed['recovery_attempted'] is True and observed['recovery_failed'] is True
    assert adapter.focused==[8] and adapter.activations==[]


def test_slow_launch_waits_for_real_window_and_activates_only_once(index_for,clock):
    index,adapter,_ = index_for()
    native_state(adapter)
    adapter.on_activate = lambda launcher:setattr(adapter,'visible',[window(9,pid=99,class_name='AppSplash')])
    clock.on_wait = lambda: setattr(adapter,'visible',[window(10,pid=100)]) if clock.elapsed>=.6 else None
    result = index.launch('Spotify',timeout=1)
    assert result['verified'] and result['window_id']==10 and result['focused']
    assert len(adapter.activations)==1 and adapter.focused==[10]
    assert .6<=clock.elapsed<=1


def test_default_launch_budget_allows_a_slow_app_without_repeated_activation(index_for,clock):
    index,adapter,_ = index_for()
    clock.on_wait = lambda: setattr(adapter,'visible',[window()]) if clock.elapsed>=13 else None
    result = index.launch('Spotify')
    assert result['verified'] and result['focused']
    assert 13<=clock.elapsed<=20 and len(adapter.activations)==1


def test_native_launch_reobserves_and_focuses_real_window_when_splash_disappears_after_focus(index_for,clock):
    index,adapter,_ = index_for()
    native_state(adapter)
    adapter.on_activate = lambda launcher:setattr(adapter,'visible',[window()])
    clock.on_wait = lambda:setattr(adapter,'visible',[window(10,pid=100,title='Loaded library')])
    result = index.launch('Spotify',timeout=1)
    assert result['verified'] and result['window_id']==10 and result['focused']
    assert adapter.focused==[8,10] and len(adapter.activations)==1


def test_launch_transition_to_unrelated_browser_is_not_focused_or_verified(index_for,clock):
    index,adapter,_ = index_for()
    native_state(adapter)
    adapter.on_activate = lambda launcher:setattr(adapter,'visible',[window()])
    clock.on_wait = lambda:setattr(adapter,'visible',[window(10,pid=100,process='chrome.exe',executable=CHROME,title='Spotify')])
    result = index.launch('Spotify',timeout=.4)
    assert result['verified'] is False and result['observed_running'] is False
    assert adapter.focused==[8] and len(adapter.activations)==1
    assert clock.elapsed<=.4


def test_genuinely_closed_app_stays_unverified_after_bounded_reobservation(index_for,clock):
    index,adapter,_ = index_for()
    result = index.observe_launch(prior_launch(index,window()),timeout=.3,recover=True)
    assert result['verified'] is False and result['observed_running'] is False
    assert .3<=clock.elapsed<=.31 and adapter.focused==[] and adapter.activations==[]


@pytest.mark.parametrize('during_launch',[False,True])
def test_cancel_during_window_wait_stops_without_new_activation_or_focus(index_for,clock,during_launch):
    import threading
    from jarvis.execution import Cancelled
    index,adapter,_ = index_for()
    class Cancellation:
        def __init__(self): self.event = threading.Event()
        def check(self):
            if self.event.is_set(): raise Cancelled('Cancelled')
    cancel = Cancellation()
    def wait(seconds):
        clock.sleep(seconds)
        cancel.event.set()
        return True
    cancel.event.wait = wait
    with pytest.raises(Cancelled):
        if during_launch: index.launch('Spotify',cancel=cancel,timeout=1)
        else: index.observe_launch(prior_launch(index,window()),cancel=cancel,timeout=.6,recover=True)
    assert len(adapter.activations)==int(during_launch) and adapter.focused==[]
    assert clock.elapsed<=.2


def test_already_cancelled_request_performs_no_window_scan_or_activation(index_for):
    from jarvis.execution import Cancelled
    index,adapter,_ = index_for(visible=[window()])
    class CancelledRequest:
        def check(self): raise Cancelled('Cancelled before launch')
    with pytest.raises(Cancelled): index.launch('Spotify',cancel=CancelledRequest())
    assert adapter.scans==0 and adapter.focused==[] and adapter.activations==[]
