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
    def observe_launch(result):
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
    assert "couldn't verify" in final_text(events)
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
    assert "couldn't verify" in final_text(events)
    assert env.control.observations==[('launch','Spotify')]
    assert env.control.launches==['Spotify'],'evaluation observes without replaying a launch'


def test_later_verified_recovery_can_complete_after_an_initial_launch_failure(env):
    env.control.results=[launch(verified=False),launch()]
    _,events=chat(env,[reply('I opened Spotify.',('launch_application',{'name':'Spotify'})),
        reply('It is open now.',('launch_application',{'name':'Spotify'})),reply('Spotify is now open.')])
    assert final_text(events)=='Spotify is now open.'
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'
    assert len(env.jarvis.state.assistant.provider.seen)==3


@pytest.mark.parametrize('claim,failed',[('Spotify is open.',False),('Spotify is open in the foreground.',True)])
def test_current_background_app_supports_open_claim_but_not_foreground_claim(env,claim,failed):
    env.control.results=[launch()];env.control.states['Spotify']=observation(focused=False)
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply(claim)])
    assert (final_text(events)!=claim)==failed
    assert env.get('/api/activity').json()['workflows'][0]['status']==('failed' if failed else 'completed')


@pytest.mark.parametrize('current_focused',[False,True])
def test_initial_foreground_denial_is_reported_unless_fresh_focus_recovers(env,current_focused):
    env.control.results=[launch(verified=False,focused=False)]
    env.control.states['Spotify']=observation(focused=current_focused)
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'})),reply('Spotify is open.')])
    assert final_text(events)==('Spotify is open.' if current_focused else
        'Spotify is running, but Windows did not allow me to bring it to the front.')
    assert env.get('/api/activity').json()['workflows'][0]['status']==('completed' if current_focused else 'failed')
    assert env.control.launches==['Spotify']


def test_opening_two_apps_does_not_require_both_to_remain_foreground(env):
    env.control.results=[launch(),launch('Outlook')]
    env.control.states.update(Spotify=observation(focused=False),Outlook=observation('Outlook',focused=True))
    text='Spotify is open. Outlook is in the foreground.'
    _,events=chat(env,[reply(None,('launch_application',{'name':'Spotify'}),('launch_application',{'name':'Outlook'})),reply(text)],text='Open Spotify and Outlook')
    assert final_text(events)==text
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
    assert final_text(events)=='Spotify is already open.'
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
    assert "couldn't verify" in final_text(events)
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
