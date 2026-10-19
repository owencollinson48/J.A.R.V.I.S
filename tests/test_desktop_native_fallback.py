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
           'capture_offset':[0,0],'client_size':[2000,1200],
           'screen_bounds':[-2560,0,3840,2160],'point_callback':None,'point_ancestors':{},'on_move':None}
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
        if state['point_callback']: return state['point_callback'](point.x,point.y)
        return state['point_hit']
    def ancestor(identity,flag):
        root,owner=state['point_ancestors'].get(identity,(state['point_root'],state['point_owner']))
        return root if flag==2 else owner
    def client_to_screen(identity,output):
        assert identity==71
        output._obj.x=state['bounds'][0]+state['capture_offset'][0]
        output._obj.y=state['bounds'][1]+state['capture_offset'][1]
        return True
    def client_rect(identity,output):
        assert identity==71
        output._obj.left=output._obj.top=0
        output._obj.right,output._obj.bottom=state['client_size']
        return True
    def screen_metric(metric):
        left,top,right,bottom=state['screen_bounds']
        return {76:left,77:top,78:right-left,79:bottom-top}[metric]
    user32=types.SimpleNamespace(WindowFromPoint=NativeCall(hit_test),
        GetAncestor=NativeCall(ancestor),
        ClientToScreen=NativeCall(client_to_screen),GetClientRect=NativeCall(client_rect),
        GetSystemMetrics=NativeCall(screen_metric))
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
    def move(*args,**kwargs):
        event('move',*args,**kwargs)
        if state['on_move']: state['on_move']()
    mouse.moveTo=move
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
    assert result['capture_method']=='window'
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


@pytest.mark.parametrize('space,x,y',[('preview',80,80),('image',100,100),('window',108,131)])
def test_referenced_pointer_translates_client_preview_and_negative_monitor_coordinates(native,space,x,y):
    desktop,state,_,_=native
    state.update(bounds=[-2400,200,-280,1450],capture_offset=[8,31])
    capture=desktop.screenshot(71)
    assert 'coordinate_space="preview"' in capture['coordinate_note']
    result=desktop.pointer(71,x,y,image_id=capture['image_id'],coordinate_space=space)
    assert state['events']==[('move',(-2292,331),{'duration':.12}),
                             ('click',(-2292,331),{'button':'left','clicks':1,'interval':0})]
    assert result['moved'] and result['clicked'] and result['verified'] is False
    assert result['image_id']!=capture['image_id'],'each coordinate input must return a fresh screenshot'
    assert desktop.latest_capture[71]==result['image_id']


def test_referenced_pointer_can_move_cursor_without_clicking(native):
    desktop,state,_,_=native
    capture=desktop.screenshot(71)
    result=desktop.pointer(71,80,80,action='move',image_id=capture['image_id'],coordinate_space='preview')
    assert state['events']==[('move',(200,300),{'duration':.12})]
    assert result['performed']=='move' and result['moved'] is True and result['clicked'] is False
    assert result['image_id']!=capture['image_id']


@pytest.mark.parametrize('change',['pid','bounds','client_size','capture_offset','minimized','expired','unknown','older','window'])
def test_referenced_screenshot_rejects_stale_or_wrong_window_before_focus_or_motion(native,change):
    desktop,state,_,_=native
    capture=desktop.screenshot(71);identity=capture['image_id']
    if change=='pid': state['window']['pid']=902
    elif change=='bounds': state['bounds'][0]+=10
    elif change=='client_size': state['client_size'][0]-=10
    elif change=='capture_offset': state['capture_offset'][0]+=10
    elif change=='minimized': state['minimized']=True
    elif change=='expired': desktop.captures[identity]['time']-=61
    elif change=='unknown': identity='0'*32
    elif change=='older': desktop.screenshot(71)
    else: desktop.captures[identity]['window_id']=72
    with pytest.raises(ValueError,match='screenshot'): desktop.pointer(71,80,80,image_id=identity,coordinate_space='preview')
    assert not state['events'] and not state['focuses']


@pytest.mark.parametrize('space,x,y',[('preview',1600,0),('preview',0,960),('preview',-1,0),
    ('image',2000,0),('image',0,1200),('image',True,0),('image',1.5,0),
    ('window',0,0),('window',2008,0),('unrecognised',0,0)])
def test_referenced_pointer_rejects_invalid_space_or_outside_captured_client(native,space,x,y):
    desktop,state,_,_=native
    state.update(bounds=[100,200,2120,1450],capture_offset=[8,31])
    capture=desktop.screenshot(71)
    with pytest.raises(ValueError): desktop.pointer(71,x,y,image_id=capture['image_id'],coordinate_space=space)
    assert not state['events'] and not state['focuses']


@pytest.mark.parametrize('space',['preview','image'])
def test_scaled_coordinates_require_screenshot_identity(native,space):
    desktop,state,_,_=native
    with pytest.raises(ValueError,match='image_id'): desktop.pointer(71,80,80,coordinate_space=space)
    assert not state['events'] and not state['focuses']


@pytest.mark.parametrize('change',['pid','bounds','client_size','capture_offset','focus','overlay','cancel','fail_safe','new_image'])
def test_pointer_rechecks_snapshot_target_and_cancellation_after_cursor_motion_before_click(native,change):
    desktop,state,_,fail_safe=native
    capture=desktop.screenshot(71);cancel=Cancellation()
    def mutate():
        if change=='pid': state['window']['pid']=902
        elif change=='bounds': state['bounds'][0]+=10
        elif change=='client_size': state['client_size'][0]-=10
        elif change=='capture_offset': state['capture_offset'][0]+=10
        elif change=='focus': state['focused']=99
        elif change=='overlay': state.update(point_root=99,point_owner=99)
        elif change=='cancel': cancel.stopped=True
        elif change=='fail_safe': state['fail_safe']=True
        else: desktop.screenshot(71)
    state['on_move']=mutate
    expected=Cancelled if change=='cancel' else fail_safe if change=='fail_safe' else ValueError
    with pytest.raises(expected): desktop.pointer(71,80,80,cancel,image_id=capture['image_id'],coordinate_space='preview')
    assert state['events']==[('move',(200,300),{'duration':.12})], 'a changed target must not receive a click'


def test_pointer_checks_exact_point_owner_again_after_cursor_motion(native):
    desktop,state,_,_=native
    desktop.pointer(71,125,85)
    assert state['points']==[(225,285),(225,285)]


@pytest.mark.parametrize('action',['click','double_click','right_click','move'])
def test_coordinate_actions_return_fresh_screenshot_even_when_accessibility_is_available(native,monkeypatch,action):
    desktop,state,_,_=native
    monkeypatch.setattr(desktop,'inspect',lambda *args:{'window':dict(state['window']),'elements':[{'id':'observed'}]})
    result=desktop.pointer(71,125,85,action=action)
    assert result['observation']['elements']==[{'id':'observed'}]
    assert result['image_id'] and len(state['captures'])==1
    assert sum(event[0]=='click' for event in state['events'])==(0 if action=='move' else 1)
    if action=='double_click': assert state['events'][-1][2]['clicks']==2


def test_failed_accessibility_inspection_still_returns_fresh_pointer_capture(native,monkeypatch):
    desktop,state,_,_=native
    def failed(*args): raise ValueError('Inspection unavailable for this custom UI')
    monkeypatch.setattr(desktop,'inspect',failed)
    result=desktop.pointer(71,125,85)
    assert result['image_id'] and len(state['captures'])==1
    assert 'Inspection unavailable' in result['observation_error']
    assert sum(event[0]=='click' for event in state['events'])==1


@pytest.mark.parametrize('action,extra',[('press_keys',{'keys':'ctrl+f'}),
    ('scroll',{'direction':'down','amount':2}),('type_text',{'text':'New page content'})])
def test_native_input_invalidates_previous_screenshot_even_when_uia_observation_needs_no_capture(native,monkeypatch,action,extra):
    desktop,state,_,_=native
    capture=desktop.screenshot(71)
    monkeypatch.setattr(desktop,'inspect',lambda *args:{'window':dict(state['window']),'elements':[{'id':'fresh-control'}]})
    args={'action':action,'window_id':71,**extra}
    result=desktop.act(args,desktop.preflight(args))
    assert result['performed']==action and 'image_id' not in result
    assert 71 not in desktop.latest_capture
    before=state['events'][:]
    with pytest.raises(ValueError,match='stale'):
        desktop.pointer(71,80,80,image_id=capture['image_id'],coordinate_space='preview')
    assert state['events']==before, 'stale pixels must not deliver another input after page mutation'


@pytest.mark.parametrize('action',['click','double_click','right_click','set_text','scroll','press_keys','close'])
def test_structured_input_invalidates_previous_screenshot_without_requiring_a_geometry_change(native,monkeypatch,action):
    desktop,state,_,_=native
    capture=desktop.screenshot(71)
    class NoPatternInterfaceError(Exception): pass
    monkeypatch.setitem(sys.modules,'pywinauto.uia_defines',types.SimpleNamespace(NoPatternInterfaceError=NoPatternInterfaceError))
    def event(name,*args,**kwargs): state['events'].append((name,args,kwargs))
    control=types.SimpleNamespace(invoke=lambda:event('invoke'),click_input=lambda **kwargs:event('click_input',**kwargs),
        iface_value=types.SimpleNamespace(SetValue=lambda text:event('set_text',text)),
        iface_scroll=types.SimpleNamespace(Scroll=lambda x,y:event('uia_scroll',x,y)))
    wrapper=types.SimpleNamespace(close=lambda:event('close'))
    monkeypatch.setattr(desktop,'preflight',lambda args:{'signature':'same-selected-control'})
    monkeypatch.setattr(desktop,'target',lambda *args:(dict(state['window']),wrapper,control,{}))
    monkeypatch.setattr(desktop,'inspect',lambda *args:{'window':dict(state['window']),'elements':[{'id':'fresh-control'}]})
    args={'action':action,'window_id':71,'element_id':'observed','text':'Updated','keys':'ctrl+f','amount':1}
    desktop.act(args,desktop.preflight(args))
    assert state['events'] and 71 not in desktop.latest_capture
    before=state['events'][:]
    with pytest.raises(ValueError,match='stale'):
        desktop.pointer(71,80,80,image_id=capture['image_id'],coordinate_space='preview')
    assert state['events']==before


def test_partial_native_input_failure_invalidates_capture_even_without_post_input_observation(native,monkeypatch):
    desktop,state,mouse,_=native
    capture=desktop.screenshot(71)
    def partial(*keys):
        state['events'].append(('partial_hotkey',keys,{}))
        raise OSError('Input interrupted after a key was sent')
    monkeypatch.setattr(mouse,'hotkey',partial)
    args={'action':'press_keys','window_id':71,'keys':'ctrl+f'}
    with pytest.raises(OSError,match='interrupted'): desktop.act(args,desktop.preflight(args))
    assert 71 not in desktop.latest_capture
    with pytest.raises(ValueError,match='stale'):
        desktop.pointer(71,80,80,image_id=capture['image_id'],coordinate_space='preview')
    assert len(state['events'])==1


@pytest.mark.parametrize('action',['click','move'])
def test_failed_post_pointer_capture_keeps_old_image_invalid_and_never_replays_input(native,monkeypatch,action):
    desktop,state,_,_=native
    capture=desktop.screenshot(71)
    def blocked(**kwargs): raise OSError('Capture blocked after delivered input')
    monkeypatch.setattr('PIL.ImageGrab.grab',blocked)
    result=desktop.pointer(71,80,80,action=action,image_id=capture['image_id'],coordinate_space='preview')
    assert result['performed']==action and result['verified'] is False
    assert 'Capture blocked' in result['observation_error'] and 'image_id' not in result
    assert 71 not in desktop.latest_capture
    before=state['events'][:]
    with pytest.raises(ValueError,match='stale'):
        desktop.pointer(71,80,80,image_id=capture['image_id'],coordinate_space='preview')
    assert state['events']==before
    assert sum(event[0]=='move' for event in state['events'])==1
    assert sum(event[0]=='click' for event in state['events'])==(1 if action=='click' else 0)


def test_read_only_inspect_preflight_and_focus_preserve_current_screenshot(native,monkeypatch):
    desktop,state,_,_=native
    capture=desktop.screenshot(71)
    monkeypatch.setattr(desktop,'inspect',lambda *args:{'window':dict(state['window']),'elements':[]})
    desktop.inspect(71);desktop.list_windows()
    desktop.preflight({'action':'press_keys','window_id':71})
    args={'action':'focus','window_id':71}
    desktop.act(args,desktop.preflight(args))
    assert desktop.latest_capture[71]==capture['image_id'] and not state['events']


def test_screenshot_registry_retains_only_eighty_recent_usable_images(native,monkeypatch):
    desktop,state,_,_=native
    state['client_size']=[100,80]
    monkeypatch.setattr('PIL.ImageGrab.grab',lambda **kwargs:Image.new('RGB',(100,80),'navy'))
    identities=[desktop.screenshot(71)['image_id'] for _ in range(81)]
    assert len(desktop.captures)==80 and identities[0] not in desktop.captures
    assert desktop.latest_capture[71]==identities[-1]
    assert len(list(desktop.images.folder.glob('*.jpg')))==80


def black_window_capture(monkeypatch,state,on_screen=None,window_error=None,toolbar_height=3):
    """Simulate a broken HWND capture while keeping screen pixels independent."""
    def capture(**kwargs):
        state['captures'].append(kwargs)
        if 'window' in kwargs:
            if window_error: raise window_error
            # A painted non-client frame must not hide a black application centre.
            image=Image.new('RGB',tuple(state['client_size']),'black')
            image.paste('white',(0,0,image.width,toolbar_height))
            return image
        if on_screen: return on_screen(kwargs)
        left,top,right,bottom=kwargs['bbox']
        return Image.new('RGB',(right-left,bottom-top),(20,90,170))
    monkeypatch.setattr('PIL.ImageGrab.grab',capture)


@pytest.mark.parametrize('toolbar_height',[3,200])
def test_black_hwnd_uses_visible_client_pixels_and_preserves_pointer_coordinates(native,monkeypatch,toolbar_height):
    desktop,state,_,_=native
    state.update(bounds=[100,200,2120,1450],capture_offset=[8,31])
    black_window_capture(monkeypatch,state,toolbar_height=toolbar_height)
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen'
    assert state['captures']==[{'window':71,'include_layered_windows':True},
        {'bbox':(108,231,2108,1431),'all_screens':True,'include_layered_windows':True}]
    assert state['uia_calls']==0 and state['focuses']
    assert result['capture_offset']==[8,31]
    assert (result['original_width'],result['original_height'])==(2000,1200)
    assert (result['width'],result['height'])==(1600,960)
    with Image.open(desktop.images.path(result['image_id'])) as preview:
        pixel=preview.getpixel((preview.width//2,preview.height//2))
        assert all(abs(channel-expected)<=3 for channel,expected in zip(pixel,(20,90,170)))
    desktop.pointer(71,108,131)
    assert ('click',(208,331),{'button':'left','clicks':1,'interval':0}) in state['events']


def test_hwnd_capture_error_recovers_from_composed_screen_without_uia(native,monkeypatch):
    desktop,state,_,_=native
    black_window_capture(monkeypatch,state,window_error=OSError('Device context capture failed'))
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen'
    assert state['uia_calls']==0 and len(state['captures'])==2
    assert desktop.images.path(result['image_id']).is_file()


def test_minimized_capture_restores_window_and_reads_its_restored_geometry(native,monkeypatch):
    desktop,state,_,_=native
    state.update(minimized=True,bounds=[-32000,-32000,-30000,-30800])
    def restore(): state.update(minimized=False,bounds=[100,200,2100,1400])
    state['on_focus']=restore
    black_window_capture(monkeypatch,state)
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen'
    assert result['bounds']==[100,200,2100,1400]
    assert state['captures'][-1]['bbox']==(100,200,2100,1400)
    assert not state['events'] and state['uia_calls']==0


def test_still_black_visible_capture_retries_once_then_publishes_no_image(native,monkeypatch):
    desktop,state,_,_=native
    black_window_capture(monkeypatch,state,on_screen=lambda kwargs:Image.new('RGB',(2000,1200),'black'))
    with pytest.raises(ValueError,match='black'): desktop.screenshot(71)
    assert sum('bbox' in capture for capture in state['captures'])==2
    assert not list(desktop.images.folder.glob('*.jpg'))
    assert desktop.captures=={} and desktop.latest_capture=={}
    assert not state['events'] and state['uia_calls']==0


def test_visible_capture_recovers_when_compositor_draws_on_the_bounded_retry(native,monkeypatch):
    desktop,state,_,_=native
    attempts=[]
    def screen(kwargs):
        attempts.append(kwargs)
        return Image.new('RGB',(2000,1200),'black' if len(attempts)==1 else 'navy')
    black_window_capture(monkeypatch,state,on_screen=screen)
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen' and len(attempts)==2
    assert desktop.images.path(result['image_id']).is_file()
    assert not state['events'] and state['uia_calls']==0


def test_black_capture_does_not_claim_visible_pixels_when_foreground_focus_is_refused(native,monkeypatch):
    desktop,state,_,_=native
    state.update(focused=99,allow_focus=False)
    black_window_capture(monkeypatch,state)
    with pytest.raises(ValueError,match='foreground focus'): desktop.screenshot(71)
    assert all('window' in capture for capture in state['captures'])
    assert not list(desktop.images.folder.glob('*.jpg'))


@pytest.mark.parametrize('change',['pid','bounds','disappear','cancel'])
def test_visible_fallback_rejects_identity_geometry_or_cancellation_changes_during_focus(native,monkeypatch,change):
    desktop,state,_,_=native
    cancel=Cancellation()
    def mutate():
        if change=='pid': state['window']['pid']=902
        elif change=='bounds': state['bounds'][0]+=10
        elif change=='disappear': state['visible']=False
        else: cancel.stopped=True
    state['on_focus']=mutate
    black_window_capture(monkeypatch,state)
    with pytest.raises(Cancelled if change=='cancel' else ValueError): desktop.screenshot(71,cancel)
    assert all('window' in capture for capture in state['captures'])
    assert not list(desktop.images.folder.glob('*.jpg'))


@pytest.mark.parametrize('change',['client_size','capture_offset'])
def test_visible_capture_uses_fresh_client_geometry_when_focus_updates_the_app(native,monkeypatch,change):
    desktop,state,_,_=native
    def mutate():
        if change=='client_size': state['client_size']=[1990,1190]
        else: state['capture_offset']=[8,31]
    state['on_focus']=mutate
    black_window_capture(monkeypatch,state)
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen'
    assert (result['original_width'],result['original_height'])==tuple(state['client_size'])
    assert result['capture_offset']==state['capture_offset']
    x,y=state['bounds'][0]+state['capture_offset'][0],state['bounds'][1]+state['capture_offset'][1]
    assert state['captures'][-1]['bbox']==(x,y,x+state['client_size'][0],y+state['client_size'][1])


@pytest.mark.parametrize('change',['pid','bounds','client_size','capture_offset','focus','overlay','cancel'])
def test_visible_fallback_revalidates_after_screen_capture_and_does_not_publish_other_pixels(native,monkeypatch,change):
    desktop,state,_,_=native
    cancel=Cancellation()
    def screen(kwargs):
        if change=='pid': state['window']['pid']=902
        elif change=='bounds': state['bounds'][0]+=10
        elif change=='client_size': state['client_size'][0]-=10
        elif change=='capture_offset': state['capture_offset'][0]+=10
        elif change=='focus': state['focused']=99
        elif change=='overlay': state.update(point_root=99,point_owner=99)
        else: cancel.stopped=True
        return Image.new('RGB',(2000,1200),'navy')
    black_window_capture(monkeypatch,state,on_screen=screen)
    with pytest.raises(Cancelled if change=='cancel' else ValueError): desktop.screenshot(71,cancel)
    assert not list(desktop.images.folder.glob('*.jpg'))


def test_visible_capture_rejects_overlay_before_capturing_screen(native,monkeypatch):
    desktop,state,_,_=native
    state.update(point_root=99,point_owner=99)
    black_window_capture(monkeypatch,state)
    with pytest.raises(ValueError,match='covered by another window'): desktop.screenshot(71)
    assert state['points'] and all('window' in capture for capture in state['captures'])
    assert not list(desktop.images.folder.glob('*.jpg'))


def test_visible_capture_allows_rounded_window_corners_but_checks_interior_ownership(native,monkeypatch):
    desktop,state,_,_=native
    state['point_ancestors'][271]=(99,99)
    # Windows 11 rounds the outer corners, exposing the desktop behind them.
    state['point_callback']=lambda x,y:271 if (x<112 or x>2087) and (y<212 or y>1387) else 171
    black_window_capture(monkeypatch,state)
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen'
    assert state['points'] and len(state['points'])>=50
    assert all(112<=x<=2087 and 212<=y<=1387 for x,y in state['points'])


def test_visible_capture_rejects_an_overlay_covering_only_its_interior(native,monkeypatch):
    desktop,state,_,_=native
    state['point_ancestors'][271]=(99,99)
    state['point_callback']=lambda x,y:271 if abs(x-1099)<3 and abs(y-799)<3 else 171
    black_window_capture(monkeypatch,state)
    with pytest.raises(ValueError,match='covered by another window'): desktop.screenshot(71)
    assert all('window' in capture for capture in state['captures'])
    assert not list(desktop.images.folder.glob('*.jpg'))


def test_visible_capture_supports_negative_monitor_coordinates(native,monkeypatch):
    desktop,state,_,_=native
    state.update(bounds=[-2400,200,-400,1400])
    black_window_capture(monkeypatch,state)
    result=desktop.screenshot(71)
    assert result['capture_method']=='visible_screen'
    assert state['captures'][-1]['bbox']==(-2400,200,-400,1400)
    assert result['bounds']==[-2400,200,-400,1400]
    assert all(-2400<=x<-400 and 200<=y<1400 for x,y in state['points'])


@pytest.mark.parametrize('bounds',[[-2600,200,-600,1400],[3000,200,5000,1400],
                                   [100,-10,2100,1190],[100,1500,2100,2700]])
def test_visible_fallback_rejects_partly_offscreen_client_area(native,monkeypatch,bounds):
    desktop,state,_,_=native
    state['bounds']=bounds
    black_window_capture(monkeypatch,state)
    with pytest.raises(ValueError): desktop.screenshot(71)
    assert all('window' in capture for capture in state['captures'])
    assert not list(desktop.images.folder.glob('*.jpg'))


def test_black_post_input_observation_failure_never_repeats_delivered_click(native,monkeypatch):
    desktop,state,_,_=native
    black_window_capture(monkeypatch,state,on_screen=lambda kwargs:Image.new('RGB',(2000,1200),'black'))
    result=desktop.pointer(71,125,85)
    assert result['performed']=='click' and result['clicked'] is True
    assert result['verified'] is False and 'black' in result['observation_error']
    assert sum(event[0]=='click' for event in state['events'])==1
    assert 'image_id' not in result and not list(desktop.images.folder.glob('*.jpg'))


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
    assert state['events']==[('move',(225,285),{'duration':.12}),
                             ('click',(225,285),{'button':'right','clicks':1,'interval':0})]
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


@pytest.mark.parametrize('change',['client_size','capture_offset','new_image'])
def test_referenced_pointer_revalidates_client_and_latest_image_after_focus_before_motion(native,change):
    desktop,state,_,_=native
    capture=desktop.screenshot(71)
    def mutate():
        if change=='client_size': state['client_size'][0]-=10
        elif change=='capture_offset': state['capture_offset'][0]+=10
        else: desktop.screenshot(71)
    state['on_focus']=mutate
    with pytest.raises(ValueError,match='screenshot'):
        desktop.pointer(71,80,80,image_id=capture['image_id'],coordinate_space='preview')
    assert not state['events']


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
