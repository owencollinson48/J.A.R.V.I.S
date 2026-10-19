"""Only evaluated, current execution evidence may become a spoken final answer."""
import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app


def reply(text, *calls):
    message={'role':'assistant','content':text}
    if calls:
        message['tool_calls']=[{'id':f'call-{time.time_ns()}-{index}','type':'function',
            'function':{'name':name,'arguments':json.dumps(args)}} for index,(name,args) in enumerate(calls)]
    return message


def launch(name='Spotify',verified=True,focused=True):
    return {'name':name,'application_id':name.casefold(), 'verified':verified,'focused':focused,
            'window_id':11 if name=='Spotify' else 12,
            'window':{'id':11 if name=='Spotify' else 12,'pid':101,'process':name.lower()+'.exe'}}


def observation(name='Spotify',verified=True,focused=True):
    return {**launch(name,verified,focused),'observed_running':verified}


class Model:
    def __init__(self,settings): self.script=[];self.seen=[]
    def headers(self): return {}
    async def complete(self,messages,tools):
        self.seen.append(messages)
        message=self.script.pop(0)
        if message.get('content'): yield {'type':'token','text':message['content']}
        yield {'type':'completion','message':message}


@pytest.fixture
def env(tmp_path):
    app=create_app(tmp_path/'private',provider_factory=Model)
    app.state.settings.update({'permissions_mode':'autonomous','native_notifications':False})
    control=types.SimpleNamespace(launches=[],results=[],observations=[],states={'Spotify':observation()})
    def activate(name,context):
        control.launches.append(name)
        return control.results.pop(0)
    def observe_launch(result,cancel=None,recover=False):
        if cancel: cancel.check()
        control.observations.append(('launch',result['name']))
        return control.states[result['name']]
    def observe_application(name):
        control.observations.append(('application',name))
        return control.states.get(name,{'name':name,'verified':False})
    app.state.tools.applications.launch=activate
    app.state.tools.applications.observe_launch=observe_launch
    app.state.tools.applications.observe_application=observe_application
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        client.jarvis,client.control=app,control
        yield client


def chat(client,script,text='Open Spotify',cid=None):
    client.jarvis.state.assistant.provider.script=script
    cid=cid or client.post('/api/conversations').json()['id']
    response=client.post(f'/api/conversations/{cid}/chat',json={'content':text})
    assert response.status_code==200,response.text
    events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert not any(event['type']=='error' for event in events),events
    return cid,events


def final_text(events):
    return next(event['content'] for event in reversed(events) if event['type']=='message')


def test_failed_launch_cannot_publish_hallucinated_success_tokens_or_saved_narrative(env):
    false_claim='Spotify is now open.'
    env.control.results=[launch(verified=False)]
    env.control.states['Spotify']=observation(verified=False)
    cid,events=chat(env,[reply(false_claim,('launch_application',{'name':'Spotify'})),reply(false_claim)])
    assert false_claim not in json.dumps([event for event in events if event['type'] in {'token','message'}])
    assert [event['text'] for event in events if event['type']=='token']==[final_text(events)],'only evaluated final prose may become a token event'
    assert final_text(events)=="I couldn't open Spotify, sir."
    history=env.jarvis.state.store.model_history(cid)
    assert next(message for message in history if message.get('tool_calls'))['content'] is None
    assert false_claim not in json.dumps(history)
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'
    assert len(env.jarvis.state.assistant.provider.seen)==2 and env.control.launches==['Spotify']
    outcome=next(event for event in events if event['type']=='outcome')
    assert outcome['workflow']['status']=='failed'
    assert events.index(outcome)<next(index for index,event in enumerate(events) if event['type']=='done')


def test_verified_launch_that_closes_before_final_answer_is_checked_again(env):
    env.control.results=[launch()];env.control.states['Spotify']=observation(verified=False)
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is now open.')])
    assert final_text(events)=='Spotify is no longer visible, sir.'
    assert env.control.observations==[('launch','Spotify')]
    assert env.control.launches==['Spotify'],'evaluation observes without replaying a launch'


def test_later_verified_recovery_can_complete_after_an_initial_launch_failure(env):
    env.control.results=[launch(verified=False),launch()]
    _,events=chat(env,[reply('I opened Spotify.',('launch_application',{'name':'Spotify'})),
        reply('It is open now.',('launch_application',{'name':'Spotify'})),reply('Spotify is now open.')])
    assert final_text(events)=='Spotify is now open for you, sir.'
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'
    assert len(env.jarvis.state.assistant.provider.seen)==3


@pytest.mark.parametrize('claim,failed',[('Spotify is open.',False),('Spotify is open in the foreground.',True)])
def test_current_background_app_supports_open_claim_but_not_foreground_claim(env,claim,failed):
    env.control.results=[launch()];env.control.states['Spotify']=observation(focused=False)
    request_text='Open Spotify and bring it to the foreground' if failed else 'Open Spotify'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply(claim)],text=request_text)
    assert final_text(events)==('Spotify is running, but I could not verify that it was brought to the front.' if failed else
        'Spotify is now open for you, sir.')
    assert env.get('/api/activity').json()['workflows'][0]['status']==('failed' if failed else 'completed')


@pytest.mark.parametrize('current_focused',[False,True])
def test_initial_foreground_denial_is_reported_unless_fresh_focus_recovers(env,current_focused):
    env.control.results=[launch(verified=False,focused=False)]
    env.control.states['Spotify']=observation(focused=current_focused)
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is open.')])
    assert final_text(events)==('Spotify is now open for you, sir.' if current_focused else
        "Spotify is running, but I couldn't bring it to the front, sir.")
    assert env.get('/api/activity').json()['workflows'][0]['status']==('completed' if current_focused else 'failed')
    assert env.control.launches==['Spotify']


@pytest.mark.parametrize('minimized',[False,True])
def test_fresh_restore_or_focus_failure_overrides_an_earlier_verified_launch(env,minimized):
    env.control.results=[launch()]
    env.control.states['Spotify']={**observation(focused=False),'minimized':minimized,
                                   'recovery_attempted':True,'recovery_failed':True}
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is now open.')])
    assert final_text(events)==("Spotify is running, but I couldn't restore its window, sir." if minimized else
                               "Spotify is running, but I couldn't bring it to the front, sir.")
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'
    assert env.control.launches==['Spotify']


def test_opening_two_apps_does_not_require_both_to_remain_foreground(env):
    env.control.results=[launch(),launch('Outlook')]
    env.control.states.update(Spotify=observation(focused=False),Outlook=observation('Outlook',focused=True))
    text='Spotify is open. Outlook is in the foreground.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'}),('launch_application',{'name':'Outlook'})),reply(text)],text='Open Spotify and Outlook')
    assert final_text(events)=='Spotify and Outlook are now open for you, sir.'
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'


def test_old_success_does_not_authorize_a_new_current_open_claim_without_tools(env):
    env.control.results=[launch()]
    cid,_=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is now open.')])
    env.control.states['Spotify']=observation(verified=False)
    _,events=chat(env,[reply('Spotify is now open.')],cid=cid)
    assert final_text(events)!='Spotify is now open.'
    assert env.control.observations[-1]==('application','Spotify')
    assert env.control.launches==['Spotify']
    assert len(env.jarvis.state.assistant.provider.seen)==3,'evaluation adds no model request'
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'


def test_current_open_app_does_not_allow_claiming_a_launch_that_never_ran(env):
    _,events=chat(env,[reply('I have opened Spotify.')])
    assert final_text(events)=='Spotify is already open, sir.'
    assert env.control.launches==[]
    assert env.control.observations==[('application','Spotify')]


@pytest.mark.parametrize('user_request,text',[
    ('Hello','Good evening, Owen.'),
    ('Write a story beginning with Spotify is open.','Spotify is open. A fictional orchestra fills the room.'),
    ('Explain how to launch Spotify.','Click Spotify to launch it, then wait for its window.'),
])
def test_normal_conversation_and_creative_text_do_not_trigger_computer_observation(env,user_request,text):
    _,events=chat(env,[reply(text)],text=user_request)
    assert final_text(events)==text
    assert env.control.observations==[] and env.control.launches==[]
    assert len(env.jarvis.state.assistant.provider.seen)==1


def test_a_plan_is_not_execution_evidence_for_an_open_claim(env):
    env.control.states['Spotify']=observation(verified=False)
    _,events=chat(env,[reply('Spotify is now open.',('plan_workflow',{'outcome':'Open Spotify','steps':['Open Spotify']})),reply('Spotify is now open.')])
    assert final_text(events)=="I can't verify that Spotify is open, sir."
    assert env.control.launches==[]


def test_unverified_native_input_cannot_be_reported_as_completed_work(env):
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Browser','unknown_target':True}
        def act(self,*args): return {'performed':'type_text','verified':False,'note':'Input was delivered, outcome unknown.'}
    env.jarvis.state.tools.desktop=Desktop()
    _,events=chat(env,[reply(None,('desktop_action',{'action':'type_text','window_id':11,'text':'Requested search'})),reply('Done, your search is complete.')],text='Type my search in the browser')
    assert final_text(events)!='Done, your search is complete.'
    assert "couldn't verify" in final_text(events)


def test_native_input_with_fresh_visual_observation_allows_model_evaluation(env):
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Browser','unknown_target':True}
        def act(self,*args): return {'performed':'type_text','verified':False,'image_id':'a'*32,
                                   'observation':{'window':{'id':11},'elements':[]}}
    env.jarvis.state.tools.desktop=Desktop()
    text='Your search text is visible in the browser.'
    _,events=chat(env,[reply(None,('desktop_action',{'action':'type_text','window_id':11,'text':'Requested search'})),reply(text)],text='Type my search in the browser')
    assert final_text(events)==text


@pytest.mark.parametrize('observed_window,observed_pid,verified',[(11,100,True),(12,100,False),(11,200,False)])
def test_later_inspection_verifies_delivered_input_only_in_the_same_window(env,observed_window,observed_pid,verified):
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Browser','unknown_target':True}
        def act(self,*args): return {'performed':'type_text','window_id':11,'verified':False,
                                   'observation':{'window':{'id':11,'pid':100},'elements':[]},
                                   'observation_error':'The app was still updating.'}
        def inspect(self,identity,cancel=None):
            return {'window':{'id':identity,'pid':observed_pid},'elements':[{'id':'new-field','text':'Requested search'}]}
    env.jarvis.state.tools.desktop=Desktop()
    text='Your search text is visible in the browser, sir.'
    _,events=chat(env,[reply(None,('desktop_action',{'action':'type_text','window_id':11,'text':'Requested search'})),
        reply(None,('inspect_window',{'window_id':observed_window})),reply(text)],text='Type my search in the browser')
    assert (final_text(events)==text)==verified
    assert env.get('/api/activity').json()['workflows'][0]['status']==('completed' if verified else 'failed')


def test_failed_accessibility_inspection_can_be_recovered_by_a_later_screenshot(env):
    from PIL import Image
    image=env.jarvis.state.tools.images.save(Image.new('RGB',(80,80),'navy'))
    class Desktop:
        def inspect(self,*args): raise OSError('Accessibility provider is unavailable')
        def screenshot(self,identity,cancel=None): return {**image,'window_id':identity}
    env.jarvis.state.tools.desktop=Desktop()
    text='The browser is ready for your next step, sir.'
    _,events=chat(env,[reply(None,('inspect_window',{'window_id':11})),
        reply(None,('desktop_screenshot',{'window_id':11})),reply(text)],text='Check the current browser window')
    assert final_text(events)==text
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'
    assert any(isinstance(message.get('content'),list) for message in env.jarvis.state.assistant.provider.seen[-1])


def test_requested_close_does_not_reopen_or_fail_a_previously_launched_app(env):
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Spotify'}
        def act(self,*args): return {'closed':True,'window_id':11}
    env.jarvis.state.tools.desktop=Desktop()
    env.control.results=[launch()];env.control.states['Spotify']=observation(verified=False)
    text='Spotify is closed, sir.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),
        reply(None,('desktop_action',{'action':'close','window_id':11})),reply(text)],text='Open Spotify, then close it')
    assert final_text(events)==text
    assert env.control.observations==[] and env.control.launches==['Spotify']
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'


def test_requested_minimization_does_not_get_reported_as_a_restore_failure(env):
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Spotify'}
        def act(self,*args): return {'performed':'minimize','verified':True,'window_id':11,'minimized':True}
    env.jarvis.state.tools.desktop=Desktop()
    env.control.results=[launch()]
    env.control.states['Spotify']={**observation(focused=False),'minimized':True}
    text='Spotify is minimized, sir.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),
        reply(None,('desktop_action',{'action':'minimize','window_id':11})),reply(text)],text='Open Spotify, then minimize it')
    assert final_text(events)==text
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'


def test_a_screenshot_alone_does_not_clear_a_failed_foreground_action(env):
    from PIL import Image
    image=env.jarvis.state.tools.images.save(Image.new('RGB',(80,80),'navy'))
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Browser'}
        def act(self,*args): return {'performed':'focus','verified':False,'window_id':11,'focused':False}
        def screenshot(self,identity,cancel=None): return {**image,'window_id':identity}
    env.jarvis.state.tools.desktop=Desktop()
    _,events=chat(env,[reply(None,('desktop_action',{'action':'focus','window_id':11})),
        reply(None,('desktop_screenshot',{'window_id':11})),reply('The app is in front, sir.')],text='Bring the app to the front')
    assert 'couldn\'t verify' in final_text(events)
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'


def test_measured_windows_blocker_is_returned_instead_of_generic_guesses(env,monkeypatch):
    calls=[]
    def diagnose(window_id=None):
        calls.append(window_id)
        return {'supported':True,'blockers':[{'code':'window_not_responding',
            'detail':'Windows reports that this window is not responding.',
            'recovery':'Wait briefly and observe again.'}]}
    monkeypatch.setattr('jarvis.windows_health.desktop_diagnostics',diagnose)
    class Desktop:
        def preflight(self,args): raise OSError('The window did not respond')
    env.jarvis.state.tools.desktop=Desktop()
    _,events=chat(env,[reply(None,('desktop_action',{'action':'press_keys','window_id':11,'keys':'ctrl+f'})),
        reply('Everything is completed.')],text='Open Find in this window')
    assert calls==[11]
    assert 'not responding' in final_text(events) and 'Everything is completed' not in final_text(events)
    assert 'administrator' not in final_text(events)
    action=env.get('/api/activity').json()['actions'][0]
    assert action['result']['diagnostics']['blockers'][0]['code']=='window_not_responding'


def test_windows_diagnostic_failure_preserves_delivered_input_and_is_not_retried(env,monkeypatch):
    inputs=[]
    def unavailable(window_id=None): raise RuntimeError('Optional diagnostic unavailable')
    monkeypatch.setattr('jarvis.windows_health.desktop_diagnostics',unavailable)
    class Desktop:
        def preflight(self,args): return {'signature':'observed','target_name':'Browser','unknown_target':True}
        def act(self,*args):
            inputs.append('typed')
            return {'performed':'type_text','window_id':11,'verified':False,'observation_error':'Paint pending'}
    env.jarvis.state.tools.desktop=Desktop()
    _,events=chat(env,[reply(None,('desktop_action',{'action':'type_text','window_id':11,'text':'Search'})),reply('Done.')],text='Type Search')
    assert inputs==['typed'] and 'couldn\'t verify' in final_text(events)
    result=env.get('/api/activity').json()['actions'][0]['result']
    assert result['performed']=='type_text' and result['observation_error']=='Paint pending' and 'error' not in result


def test_file_open_claim_is_not_rewritten_as_missing_app_launch(env,tmp_path):
    path=tmp_path/'notes.txt';path.write_text('My notes')
    env.jarvis.state.tools.computer.open_file=lambda **kwargs:{'opened':str(path)}
    text='I opened notes.txt.'
    _,events=chat(env,[reply(None,('open_file',{'path':str(path)})),reply(text)],text='Open notes.txt')
    assert final_text(events)==text


def test_browser_open_claim_is_not_rewritten_as_missing_app_launch(env):
    class Browser:
        def run(self,operation,*args):
            return {'signature':'observed','target_name':'navigate'} if operation=='preflight' else {'verified':True,'performed':'navigate'}
        def close(self): pass
    env.jarvis.state.tools.browser=Browser()
    text='I opened Example in your browser.'
    _,events=chat(env,[reply(None,('browser_action',{'action':'navigate','url':'https://example.com'})),reply(text)],text='Open Example')
    assert final_text(events)==text


def test_old_spotify_history_cannot_replace_current_verified_outlook_receipt(env):
    env.control.results=[launch()]
    cid,_=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is open.')])
    env.control.results=[launch('Outlook')]
    env.control.states.update(Spotify=observation(verified=False),Outlook=observation('Outlook'))
    stale='Spotify is now open.'
    _,events=chat(env,[reply(stale,('launch_application',{'name':'Outlook'})),reply(stale)],text='Open Outlook',cid=cid)
    assert final_text(events)=='Outlook is now open for you, sir.'
    assert stale not in json.dumps([event for event in events if event['type'] in {'message','token'}])
    history=env.jarvis.state.store.model_history(cid)
    last_user=max(index for index,message in enumerate(history) if message['role']=='user')
    assert stale not in json.dumps(history[last_user:])
    assert env.control.observations[-1]==('launch','Outlook')
    assert env.control.launches==['Spotify','Outlook']
    assert len(env.jarvis.state.assistant.provider.seen)==4


def test_simple_verified_app_gets_short_receipt_without_screenshot_narration(env):
    env.control.results=[launch('Outlook')];env.control.states['Outlook']=observation('Outlook')
    verbose='The screenshot shows Outlook is open. I have inspected the application window and completed the requested operation successfully. Let me know what you would like next.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),reply(verbose)],text='Could you open the Outlook app for me please?')
    assert final_text(events)=='Outlook is now open for you, sir.'
    assert 'screenshot' not in final_text(events).lower()
    assert len(env.jarvis.state.assistant.provider.seen)==2


@pytest.mark.parametrize('user_request',[
    'Open Outlook',
    'Open Outlook, thanks.',
    'Open Outlook, thank you.',
    'Open Outlook when you can.',
    'Could you open Outlook when you get a chance, thanks?',
])
def test_courtesy_suffix_is_not_mistaken_for_another_requested_app(env,user_request):
    env.control.results=[launch('Outlook')];env.control.states['Outlook']=observation('Outlook')
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),reply('Outlook is open.')],text=user_request)
    assert final_text(events)=='Outlook is now open for you, sir.'
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'


@pytest.mark.parametrize('user_request,missing',[
    ('Open Outlook and Calculator, thanks.','Calculator'),
    ('Open Unknown App when you can.','Unknown'),
])
def test_courtesy_handling_does_not_confirm_unknown_or_missing_apps(env,user_request,missing):
    env.control.results=[launch('Outlook')];env.control.states['Outlook']=observation('Outlook')
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),reply('Everything is open.')],text=user_request)
    assert final_text(events)==f"I couldn't open {missing}, sir."
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'


def test_successful_wrong_application_does_not_complete_requested_app(env):
    env.control.results=[launch('Spotify')]
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is open.')],text='Open Outlook')
    assert final_text(events)=="I couldn't open Outlook, sir."
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'


def test_known_canonical_app_cannot_be_confirmed_with_another_apps_identity(env):
    store=env.jarvis.state.store
    for identity,name in [('spotify','Spotify'),('google chrome','Google Chrome')]:
        store.execute('INSERT INTO applications(id,name,payload,updated_at) VALUES(?,?,?,?)',
            (identity,name,json.dumps({'id':identity,'name':name}),'2026-10-20T00:00:00+00:00'))
    env.control.results=[launch('Google Chrome')];env.control.states['Google Chrome']=observation('Google Chrome')
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is now open.')])
    assert final_text(events)=="I couldn't open Spotify, sir."


@pytest.mark.parametrize('alias',['work mail','my work mail'])
def test_explicit_user_alias_preserves_short_confirmation_of_actual_app(env,alias):
    env.jarvis.state.store.execute("INSERT INTO application_aliases(alias,application_id,source) VALUES(?,?,'user')",(alias,'outlook'))
    env.control.results=[launch('Outlook')];env.control.states['Outlook']=observation('Outlook')
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),reply('Outlook is open.')],text='Open '+alias)
    assert final_text(events)=='Outlook is now open for you, sir.'


@pytest.mark.parametrize('stale_claim',['Spotify is now open.','Outlook is now open.'])
def test_explicit_correction_uses_current_corrected_target_instead_of_stale_claim(env,stale_claim):
    env.control.states.update(Spotify=observation(verified=False),Outlook=observation('Outlook'))
    _,events=chat(env,[reply(stale_claim)],text='Spotify is not open')
    assert final_text(events)=="I can't verify that Spotify is open, sir."
    assert env.control.observations==[('application','Spotify')]
    assert env.control.launches==[]


def test_correction_with_fresh_background_window_reports_that_limit(env):
    env.control.states['Spotify']=observation(focused=False)
    _,events=chat(env,[reply('Spotify is now open.')],text="Spotify isn't open")
    assert final_text(events)=="Spotify is running, but it isn't in front, sir."
    assert env.control.launches==[]


def test_current_request_without_new_tool_still_uses_current_app_not_stale_name(env):
    env.control.states['Outlook']=observation('Outlook')
    _,events=chat(env,[reply('Spotify is now open.')],text='Open Outlook')
    assert final_text(events)=='Outlook is already open, sir.'
    assert env.control.observations==[('application','Outlook')]
    assert env.control.launches==[]


@pytest.mark.parametrize('outlook_open',[True,False])
def test_addressing_jarvis_without_a_tool_still_checks_current_requested_app(env,outlook_open):
    env.control.states['Outlook']=observation('Outlook',verified=outlook_open)
    _,events=chat(env,[reply('Spotify is now open.')],text='Jarvis, open Outlook')
    assert final_text(events)==('Outlook is already open, sir.' if outlook_open else
        "I can't verify that Outlook is open, sir.")
    assert env.control.observations==[('application','Outlook')]
    assert env.control.launches==[]
    assert 'Spotify' not in json.dumps([event for event in events if event['type'] in {'message','token'}])


def test_failed_current_outlook_launch_never_reuses_spotify_success(env):
    env.control.results=[launch('Outlook',verified=False)]
    env.control.states['Outlook']=observation('Outlook',verified=False)
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),reply('Spotify is now open.')],text='Open Outlook')
    assert final_text(events)=="I couldn't open Outlook, sir."
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'


def test_substantive_work_inside_app_keeps_its_actual_answer(env):
    env.control.results=[launch('Outlook')];env.control.states['Outlook']=observation('Outlook')
    env.jarvis.state.tools.desktop.search_outlook=lambda **kwargs:{'messages':[{'subject':'Review the proposal'},{'subject':'Project deadline'}]}
    text='Your latest messages concern reviewing the proposal and the project deadline. The deadline message asks for your reply by Friday.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),
        reply(None,('search_outlook',{'query':'','limit':2})),reply(text)],text='Open Outlook and read my latest emails')
    assert final_text(events)==text
    assert len(env.jarvis.state.assistant.provider.seen)==3


@pytest.mark.parametrize('user_request',[
    'Open Outlook and explain what the Outbox does',
    'Open Outlook. What does the Outbox do?',
    "Explain what Outlook's Outbox does. Open Outlook.",
])
def test_app_opening_with_an_explanation_keeps_substantive_answer_without_extra_tools(env,user_request):
    env.control.results=[launch('Outlook')];env.control.states['Outlook']=observation('Outlook')
    text='Outlook is open. The Outbox holds messages waiting to be sent; sent messages move to Sent Items.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Outlook'})),reply(text)],text=user_request)
    assert final_text(events)==text
