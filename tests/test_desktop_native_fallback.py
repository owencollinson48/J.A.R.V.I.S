"""UIA outage recovery contracts; Win32/window capture/input are simulated."""
import json
import sys
import types

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from jarvis import desktop as desktop_module
from jarvis.app import create_app
from jarvis.applications import WindowsApplications
from jarvis.desktop import DesktopControl, Images
from jarvis.execution import Cancelled


@pytest.fixture
def native(tmp_path,monkeypatch):
    state={'window':{'id':71,'pid':901,'title':'Current browser','process':'chrome.exe',
                    'executable':r'C:\Browser\chrome.exe'},
           'bounds':[100,200,2100,1400],'focused':71,'visible':True,'allow_focus':True,
           'minimized':False,'maximized':False,'fullscreen':False,'focuses':[],
           'sizes':[],'events':[],'captures':[],'uia_calls':0,'fail_safe':False,'on_focus':None,
           'unicode_callbacks':[],'point_hit':171,'point_root':71,'point_owner':71,'points':[],
           'capture_offset':[0,0]}
    desktop=DesktopControl(Images(tmp_path))
    monkeypatch.setattr(desktop,'require_windows',lambda:None)
    monkeypatch.setattr(WindowsApplications,'windows',lambda:[dict(state['window'])] if state['visible'] else [])
    def window_state(identity):
        assert identity==71
        return {'window_id':identity,'bounds':state['bounds'][:],
                'focused':state['focused']==identity,
                **{key:state[key] for key in ('minimized','maximized','fullscreen')}}
    monkeypatch.setattr(WindowsApplications,'window_state',window_state)
    monkeypatch.setattr(WindowsApplications,'foreground_window',lambda:state['focused'])
    def focus(identity):
        state['focuses'].append(identity)
        if state['allow_focus']: state['focused']=identity
        if state['on_focus']: state['on_focus']()
        return state['focused']==identity
    monkeypatch.setattr(WindowsApplications,'focus',focus)
    def ensure_focus(identity):
        if not focus(identity): raise ValueError('Windows did not allow foreground focus')
    monkeypatch.setattr(desktop,'ensure_focus',ensure_focus)
    class NativeCall:
        def __init__(self,callback): self.callback=callback
        def __call__(self,*args): return self.callback(*args)
    def hit_test(point):
        state['points'].append((point.x,point.y))
        return state['point_hit']
    def client_to_screen(identity,output):
        assert identity==71
        output._obj.x=state['bounds'][0]+state['capture_offset'][0]
        output._obj.y=state['bounds'][1]+state['capture_offset'][1]
        return True
    user32=types.SimpleNamespace(WindowFromPoint=NativeCall(hit_test),
        GetAncestor=NativeCall(lambda identity,flag:state['point_root'] if flag==2 else state['point_owner']),
        ClientToScreen=NativeCall(client_to_screen))
    monkeypatch.setattr('jarvis.desktop.ctypes.WinDLL',lambda *args,**kwargs:user32,raising=False)
    def size_window(identity,action):
        state['sizes'].append(action)
        state['minimized']=action=='minimize';state['maximized']=action=='maximize'
        return {'performed':action,'verified':True,**window_state(identity)}
    monkeypatch.setattr(WindowsApplications,'size_window',size_window)
    def unavailable():
        state['uia_calls']+=1
        raise desktop_module.AccessibilityError('Synthetic UIA COM initialization failure')
    monkeypatch.setattr(desktop,'automation',unavailable)
    # Native operations cannot depend even on importing UIA's exception class.
    monkeypatch.setitem(sys.modules,'pywinauto',None)
    monkeypatch.setitem(sys.modules,'pywinauto.uia_defines',None)
    class FailSafeException(Exception): pass
    def fail_safe():
        if state['fail_safe']: raise FailSafeException('Pointer is in a fail-safe corner')
    mouse=types.SimpleNamespace(FAILSAFE=False,failSafeCheck=fail_safe,
        KEYBOARD_KEYS=['ctrl','f','enter','tab','alt','left','right','escape','f11'])
    def event(name,*args,**kwargs):
        fail_safe();state['events'].append((name,args,kwargs))
    mouse.click=lambda *args,**kwargs:event('click',*args,**kwargs)
    mouse.moveTo=lambda *args,**kwargs:event('move',*args,**kwargs)
    mouse.scroll=lambda *args,**kwargs:event('scroll',*args,**kwargs)
    mouse.hscroll=lambda *args,**kwargs:event('hscroll',*args,**kwargs)
    mouse.hotkey=lambda *args,**kwargs:event('hotkey',*args,**kwargs)
    def press(key):
        event('press',key)
        if key=='f11': state['fullscreen']=not state['fullscreen']
    mouse.press=press
    monkeypatch.setitem(sys.modules,'pyautogui',mouse)
    def unicode_input(text,cancel=None,window_id=None,safety_check=None):
        state['unicode_callbacks'].append(safety_check)
        assert safety_check is not None,'native text batches must preserve the emergency pointer fail-safe'
        if cancel: cancel.check()
        safety_check()
        event('unicode',text)
    monkeypatch.setattr('jarvis.existing_browser.send_unicode_text',unicode_input)
    def capture(**kwargs):
        state['captures'].append(kwargs)
        return Image.new('RGB',(2000,1200),'navy')
    monkeypatch.setattr('PIL.ImageGrab.grab',capture)
    return desktop,state,mouse,FailSafeException


@pytest.mark.parametrize('action',['focus','maximize','minimize','restore','fullscreen'])
def test_window_controls_succeed_without_uia_or_pywinauto(native,action):
    desktop,state,_,_=native
    args={'action':action,'window_id':71}
    preview=desktop.preflight(args)
    result=desktop.act(args,preview)
    assert result['performed']==action and result['verified']
    assert state['uia_calls']==0,'window controls must not initialize the unavailable UIA backend'
    if action in {'maximize','minimize','restore'}: assert state['sizes']==[action]
    if action=='fullscreen': assert state['fullscreen']


def test_screenshot_targets_only_requested_hwnd_and_reports_original_pixel_size(native):
    desktop,state,_,_=native
    result=desktop.screenshot(71)
    assert state['uia_calls']==0
    assert state['captures']==[{'window':71,'include_layered_windows':True}]
    assert result['window_id']==71 and result['title']=='Current browser'
    assert result['bounds']==[100,200,2100,1400]
    assert (result['original_width'],result['original_height'])==(2000,1200)
    assert (result['width'],result['height'])==(1600,960),'saved preview is scaled while coordinates retain native pixels'
    assert desktop.images.path(result['image_id']).is_file()


def test_screenshot_client_offset_and_preview_scale_map_to_outer_window_pointer_pixels(native):
    desktop,state,_,_=native
    state.update(bounds=[100,200,2120,1450],capture_offset=[8,31])
    capture=desktop.screenshot(71)
    assert capture['capture_offset']==[8,31]
    assert 'capture_offset[0]' in capture['coordinate_note'] and 'capture_offset[1]' in capture['coordinate_note']
    x=int(80*capture['original_width']/capture['width']+capture['capture_offset'][0])
    y=int(80*capture['original_height']/capture['height']+capture['capture_offset'][1])
    desktop.pointer(71,x,y)
    assert ('click',(208,331),{'button':'left','clicks':1,'interval':0}) in state['events']


@pytest.mark.parametrize('change',['pid','bounds','cancel'])
def test_window_changes_or_cancellation_during_capture_do_not_publish_image(native,monkeypatch,change):
    desktop,state,_,_=native
    cancel=Cancellation()
    def capture(**kwargs):
        state['captures'].append(kwargs)
        if change=='pid': state['window']['pid']=902
        elif change=='bounds': state['bounds'][0]+=10
        else: cancel.stopped=True
        return Image.new('RGB',(2000,1200),'navy')
    monkeypatch.setattr('PIL.ImageGrab.grab',capture)
    with pytest.raises(Cancelled if change=='cancel' else ValueError): desktop.screenshot(71,cancel)
    assert not list(desktop.images.folder.glob('*.jpg'))


def test_inspection_exposes_native_window_and_visual_recovery_when_uia_is_unavailable(native):
    desktop,state,_,_=native
    observed=desktop.inspect(71)
    assert observed['window']['id']==71 and observed['window']['pid']==901
    assert observed['elements']==[] and observed['limited']
    assert observed['accessibility']['available'] is False
    assert 'UIA COM' in observed['accessibility']['error']
    assert 'desktop_screenshot' in observed['note']
    assert 'desktop_pointer' in observed['note']
    assert state['events']==[]


@pytest.mark.parametrize('operation',['inspect','pointer'])
def test_accessibility_enumeration_failure_also_allows_visual_recovery(native,monkeypatch,operation):
    desktop,state,_,_=native
    class DisconnectedProvider:
        def descendants(self,**kwargs): raise OSError('UIA provider disconnected during enumeration')
    monkeypatch.setattr(desktop,'window',lambda identity:(dict(state['window']),DisconnectedProvider()))
    result=desktop.inspect(71) if operation=='inspect' else desktop.pointer(71,125,85)
    observed=result if operation=='inspect' else result['observation']
    assert observed['accessibility']['available'] is False
    assert 'disconnected' in observed['accessibility']['error']
    assert observed['elements']==[] and observed['window']['id']==71
    if operation=='pointer':
        assert any(event[0]=='click' for event in state['events'])
        assert desktop.images.path(result['image_id']).is_file()


def test_accessibility_enumeration_does_not_swallow_cancellation(native,monkeypatch):
    desktop,state,_,_=native
    class CancelledProvider:
        def descendants(self,**kwargs): raise Cancelled('Stopped during inspection')
    monkeypatch.setattr(desktop,'window',lambda identity:(dict(state['window']),CancelledProvider()))
    with pytest.raises(Cancelled): desktop.inspect(71)


def test_window_relative_pointer_delivers_input_and_returns_visual_observation_without_uia(native):
    desktop,state,mouse,_=native
    result=desktop.pointer(71,125,85,action='right_click')
    assert state['events']==[('click',(225,285),{'button':'right','clicks':1,'interval':0})]
    assert mouse.FAILSAFE is True
    assert result['performed']=='right_click'
    assert result['verified'] is False,'delivered input is not proof that its requested outcome happened'
    assert result['observation']['accessibility']['available'] is False
    assert result['observation']['window']['id']==71
    assert desktop.images.path(result['image_id']).is_file()
    assert all(capture['window']==71 for capture in state['captures'])


def test_blocked_post_click_capture_preserves_delivered_input_without_replay(native,monkeypatch):
    desktop,state,_,_=native
    def blocked(**kwargs): raise OSError('Synthetic window capture blocked')
    monkeypatch.setattr('PIL.ImageGrab.grab',blocked)
    result=desktop.pointer(71,125,85)
    assert result['performed']=='click' and result['clicked'] is True
    assert result['verified'] is False and 'capture blocked' in result['observation_error']
    assert 'without repeating the input' in result['note']
    assert sum(event[0]=='click' for event in state['events'])==1
    assert 'image_id' not in result


@pytest.mark.parametrize('x,y',[(2000,0),(0,1200),(-1,0),(0,-1),(True,0),(1.2,0)])
def test_native_pointer_rejects_outside_or_noninteger_coordinates(native,x,y):
    desktop,state,_,_=native
    with pytest.raises(ValueError): desktop.pointer(71,x,y)
    assert not state['events']


@pytest.mark.parametrize('change',['pid','bounds','disappear'])
def test_pointer_revalidates_window_identity_and_bounds_after_focusing(native,change):
    desktop,state,_,_=native
    def mutate():
        if change=='pid': state['window']['pid']=902
        elif change=='bounds': state['bounds'][0]+=10
        else: state['visible']=False
    state['on_focus']=mutate
    with pytest.raises(ValueError): desktop.pointer(71,125,85)
    assert not state['events'],'a stale or different window must not receive the click'


def test_window_control_preflight_rejects_reused_hwnd_before_acting(native):
    desktop,state,_,_=native
    args={'action':'maximize','window_id':71};preview=desktop.preflight(args)
    state['window']['pid']=902
    with pytest.raises(ValueError): desktop.act(args,preview)
    assert state['sizes']==[] and state['focuses']==[]


def test_denied_foreground_focus_does_not_send_pointer_input(native):
    desktop,state,_,_=native
    state.update(focused=99,allow_focus=False)
    with pytest.raises(ValueError,match='foreground focus'): desktop.pointer(71,125,85)
    assert not state['events']


@pytest.mark.parametrize('hit',[0,171])
def test_foreground_window_point_covered_by_unrelated_window_is_not_clicked(native,hit):
    desktop,state,_,_=native
    state.update(point_hit=hit,point_root=99,point_owner=99)
    with pytest.raises(ValueError,match='covered by another window'): desktop.pointer(71,125,85)
    assert state['points']==[(225,285)]
    assert state['focused']==71,'a foreground window can still have an unrelated topmost overlay'
    assert state['events']==[]


def test_native_pointer_preserves_emergency_fail_safe(native):
    desktop,state,mouse,fail_safe=native
    state['fail_safe']=True
    with pytest.raises(fail_safe): desktop.pointer(71,125,85)
    assert mouse.FAILSAFE is True and not state['events']


class Cancellation:
    stopped=False
    def check(self):
        if self.stopped: raise Cancelled('Stopped by user')


def test_cancellation_before_capture_or_input_prevents_side_effects(native):
    desktop,state,_,_=native
    cancel=Cancellation();cancel.stopped=True
    with pytest.raises(Cancelled): desktop.screenshot(71,cancel)
    with pytest.raises(Cancelled): desktop.pointer(71,125,85,cancel)
    assert not state['captures'] and not state['events'] and not state['focuses']


def test_cancellation_between_foreground_focus_and_click_prevents_input(native):
    desktop,state,_,_=native
    cancel=Cancellation();state['on_focus']=lambda:setattr(cancel,'stopped',True)
    with pytest.raises(Cancelled): desktop.pointer(71,125,85,cancel)
    assert not state['events']


@pytest.mark.parametrize('action,extra',[('press_keys',{'keys':'ctrl+f'}),('scroll',{'direction':'down','amount':3})])
def test_window_keyboard_and_scroll_continue_with_visual_observation_without_uia(native,action,extra):
    desktop,state,mouse,_=native
    args={'action':action,'window_id':71,**extra}
    result=desktop.act(args,desktop.preflight(args))
    assert result['performed']==action
    assert result['observation']['accessibility']['available'] is False
    assert mouse.FAILSAFE is True
    if action=='press_keys': assert ('hotkey',('ctrl','f'),{}) in state['events']
    else: assert sum(event[0]=='scroll' for event in state['events'])==3


def test_native_unicode_text_keeps_literal_user_text_and_returns_visual_observation(native):
    desktop,state,mouse,_=native
    text='Hello {ENTER}+^% ~ Ω café 👋\nNext line'
    args={'action':'type_text','window_id':71,'text':text}
    result=desktop.act(args,desktop.preflight(args))
    assert state['events']==[('unicode',(text,),{})],'text must never be parsed as SendKeys or hotkeys'
    assert state['unicode_callbacks']==[mouse.failSafeCheck]
    assert result['performed']=='type_text' and result['verified'] is False
    assert result['observation']['accessibility']['available'] is False
    assert desktop.images.path(result['image_id']).is_file()


def test_native_text_is_bounded_and_checks_cancellation_after_focus(native):
    desktop,state,_,_=native
    args={'action':'type_text','window_id':71,'text':'x'*12001}
    with pytest.raises(ValueError): desktop.act(args,desktop.preflight(args))
    assert state['events']==[]
    args['text']='Requested text';cancel=Cancellation()
    state['on_focus']=lambda:setattr(cancel,'stopped',True)
    with pytest.raises(Cancelled): desktop.act(args,desktop.preflight(args),cancel)
    assert state['events']==[]


def test_unavailable_uia_does_not_create_fake_element_ids_for_targeted_text(native):
    desktop,state,_,_=native
    with pytest.raises(ValueError):
        desktop.preflight({'action':'set_text','window_id':71,'element_id':'unobserved','text':'Requested text'})
    assert not state['events']


def test_assistant_recovers_from_unavailable_uia_through_screenshot_and_native_input(native,tmp_path):
    desktop,state,_,_=native
    steps=[('inspect_window',{'window_id':71}),('desktop_screenshot',{'window_id':71}),
           ('desktop_pointer',{'window_id':71,'x':125,'y':85}),
           ('desktop_action',{'action':'type_text','window_id':71,'text':'Research café design Ω'}),
           ('desktop_action',{'action':'press_keys','window_id':71,'keys':'ctrl+f'})]
    class RecoveryModel:
        def __init__(self,settings): self.seen=[]
        def headers(self): return {}
        async def complete(self,messages,tools):
            self.seen.append(messages);index=len(self.seen)-1
            if index==1:
                previous=json.loads(next(message['content'] for message in reversed(messages) if message['role']=='tool'))
                assert previous['accessibility']['available'] is False
            if index==2:
                assert any(isinstance(message.get('content'),list) and
                    any(part.get('type')=='image_url' for part in message['content']) for message in messages)
            if index<len(steps):
                name,args=steps[index]
                message={'role':'assistant','content':None,'tool_calls':[{'id':f'recover-{index}',
                    'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
            else:
                message={'role':'assistant','content':'Inputs were sent. The final outcome is not verified because accessibility is unavailable.'}
            yield {'type':'completion','message':message}
    app=create_app(tmp_path/'private',provider_factory=RecoveryModel)
    app.state.settings.update({'permissions_mode':'autonomous','native_notifications':False})
    app.state.tools.desktop=desktop;app.state.tools.images=desktop.images
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        cid=client.post('/api/conversations').json()['id']
        response=client.post(f'/api/conversations/{cid}/chat',json={'content':'Click the browser search field, type Research café design Ω, then open Find and report what you can verify.'})
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert not any(event['type'] in {'error','approval'} for event in events),events
        assert len(app.state.assistant.provider.seen)==6
        assert any(event[0]=='click' for event in state['events'])
        assert ('hotkey',('ctrl','f'),{}) in state['events']
        assert ('unicode',('Research café design Ω',),{}) in state['events']
        assert all(capture['window']==71 for capture in state['captures'])
        activity=client.get('/api/activity').json()
        assert len(activity['actions'])==5
        pointer=next(action for action in activity['actions'] if action['name']=='desktop_pointer')
        assert pointer['result']['verified'] is False
        assert desktop.images.path(pointer['result']['image_id']).is_file()
