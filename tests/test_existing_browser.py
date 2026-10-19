"""Current-browser routing preserves the active browser/session and existing tab."""
import types
import pytest
from jarvis.applications import WindowsApplications
from jarvis.browser import BrowserControl
from jarvis.config import Settings
from jarvis.desktop import Images
from jarvis.existing_browser import ExistingBrowser

@pytest.fixture
def opened(monkeypatch):
    windows=[{'id':8,'pid':80,'title':'JARVIS - Google Chrome','process':'chrome.exe'},
             {'id':9,'pid':90,'title':'Signed in - Microsoft Edge','process':'msedge.exe'}]
    foreground=[9];keys=[];typed=[];focused=[];actual=[None];native=[]
    class Desktop:
        def ensure_focus(self,identity): focused.append(identity)
        def inspect(self,identity,cancel=None): return {'window':next(item for item in windows if item['id']==identity),'elements':[{'id':'observed','name':'Page button','type':'Button'}]}
        def preflight(self,args): return {'signature':'observed','target_name':'Page button'}
        def act(self,args,preview,cancel=None): native.append(args);return {'performed':args['action']}
        def screenshot(self,identity,cancel=None): return {'image_id':'a'*32,'window_id':identity}
    monkeypatch.setattr(WindowsApplications,'windows',lambda:windows[:])
    monkeypatch.setattr(WindowsApplications,'foreground_window',lambda:foreground[0])
    browser=ExistingBrowser(Desktop());browser.address=lambda identity:actual[0]
    browser.type_url=lambda url:typed.append(url)
    def key(*value):
        keys.append(value)
        if value==('enter',): actual[0]=typed[-1]
    browser.keys=key
    return types.SimpleNamespace(browser=browser,windows=windows,foreground=foreground,keys=keys,typed=typed,focused=focused,native=native)

def test_uses_foreground_browser_and_new_tab_without_overwriting_current_session(opened):
    args={'action':'navigate','url':'https://example.com/app?value=100%25'}
    preview=opened.browser.preflight(args)
    result=opened.browser.act(args,preview)
    assert preview['window_id']==9 and result['window_id']==9
    assert opened.keys==[('ctrl','t'),('ctrl','l'),('enter',)]
    assert opened.typed==[args['url']] and opened.focused==[9]
    assert result['verified'] and result['mode']=='existing'

def test_dashboard_tab_is_preserved_by_new_tab_navigation(opened):
    opened.foreground[0]=8
    args={'action':'navigate','url':'https://example.com'}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['window_id']==8 and opened.keys[0]==('ctrl','t')

def test_observed_controls_are_routed_to_same_browser_native_accessibility(opened):
    args={'action':'fill','element_id':'observed','text':'Requested text'}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert opened.native==[{'action':'set_text','element_id':'observed','text':'Requested text','window_id':9}]
    assert result['mode']=='existing' and result['observation']['elements'][0]['id']=='observed'

@pytest.mark.parametrize('url',['javascript:alert(1)','file:///C:/secret','https://user:password@example.com','https://example.com/\nrun'])
def test_current_browser_rejects_executable_or_embedded_credential_urls(opened,url):
    with pytest.raises(ValueError): opened.browser.preflight({'action':'navigate','url':url})
    assert not opened.keys and not opened.typed

def test_closed_browser_between_preflight_and_navigation_is_not_replaced_silently(opened):
    args={'action':'navigate','url':'https://example.com'};preview=opened.browser.preflight(args)
    opened.windows[:]=[]
    with pytest.raises(ValueError,match='closed'):opened.browser.act(args,preview)
    assert not opened.keys

def test_windows_current_mode_never_starts_playwright_profile(tmp_path,opened,monkeypatch):
    settings=Settings(tmp_path/'private');browser=BrowserControl(settings,Images(settings.data_dir))
    browser.existing=opened.browser
    monkeypatch.setattr('jarvis.browser.platform.system',lambda:'Windows')
    browser._open=lambda:pytest.fail('Current-browser mode must not start a separate managed browser')
    args={'action':'navigate','url':'https://example.com'}
    try:
        result=browser.run('act',args,browser.run('preflight',args))
        assert result['mode']=='existing' and result['verified']
        assert browser.playwright is None and browser.context is None
    finally: browser.close()


def test_no_open_browser_uses_windows_default_without_creating_managed_profile(opened,monkeypatch):
    opened.windows.clear();launched=[]
    url='https://example.com/new'
    def open_default(value,new=0):
        launched.append((value,new))
        opened.windows.append({'id':9,'pid':90,'title':'New browser','process':'msedge.exe'})
        return True
    monkeypatch.setattr('webbrowser.open',open_default)
    monkeypatch.setattr(WindowsApplications,'focus',lambda identity:opened.focused.append(identity))
    opened.browser.address=lambda identity:url
    args={'action':'navigate','url':url}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert launched==[(url,2)] and result['verified'] and result['window_id']==9
    assert not opened.keys
