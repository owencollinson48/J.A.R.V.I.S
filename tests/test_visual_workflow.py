"""A real chat/tool round trip displays a saved graphic before its spoken reply."""
import json

import pytest

from test_response_evaluation import chat, env, final_text, reply


def day_visual(label='Prepare project proposal'):
    return {'kind':'timeline','title':'Your day','subtitle':'Saved tasks and a suggested flexible block',
            'items':[{'time':'09:00','label':label,'detail':'Scheduled task','status':'planned'},
                     {'time':'Flexible','label':'Review priorities','detail':'Suggested, not scheduled'}]}


@pytest.mark.parametrize('mode',['autonomous','review'])
def test_requested_day_visual_uses_planner_data_and_arrives_before_the_spoken_reply(env,mode):
    env.jarvis.state.settings.update({'permissions_mode':mode})
    store=env.jarvis.state.store
    task=store.create_item('tasks',{'title':'Prepare project proposal','due_at':'2026-10-20T09:00:00+11:00'})
    before={table:store.rows(f'SELECT * FROM {table}') for table in ('tasks','goals','reminders')}
    spoken='Your proposal is scheduled for nine, sir. I suggest a flexible block afterwards to review your priorities.'
    cid,events=chat(env,[reply(None,('list_planner',{})),reply(None,('show_visual',day_visual(task['title']))),
                         reply(spoken)],text='Show an outline of my day and read it out')
    visuals=[event for event in events if event['type']=='visual']
    assert len(visuals)==1 and visuals[0]['visual']['items'][0]['label']==task['title']
    assert events.index(visuals[0]) < next(index for index,event in enumerate(events) if event['type']=='message')
    assert final_text(events)==spoken and not any(event['type']=='approval' for event in events)
    assert env.get('/api/conversations/'+cid).json()['visual']==visuals[0]['visual']
    assert env.get('/api/activity').json()['workflows'][0]['status']=='completed'
    assert before=={table:store.rows(f'SELECT * FROM {table}') for table in before},'displaying a plan does not schedule it'
    planner_result=json.loads(next(message['content'] for message in
        env.jarvis.state.assistant.provider.seen[1] if message['role']=='tool'))
    assert planner_result['tasks'][0]['id']==task['id']


def test_visual_can_be_replaced_and_dismissed_without_revealing_the_older_graphic(env):
    cid,events=chat(env,[reply(None,('show_visual',day_visual())),reply('Here is your outline, sir.')],text='Show my day visually')
    first=next(event['visual'] for event in events if event['type']=='visual')
    updated={**day_visual(),'title':'Revised outline','items':[{'label':'Afternoon focus','time':'Flexible'}]}
    _,events=chat(env,[reply(None,('show_visual',updated)),reply('I have updated the outline, sir.')],
                  text='Move the suggested focus block to the afternoon',cid=cid)
    latest=next(event['visual'] for event in events if event['type']=='visual')
    assert latest['id']!=first['id']
    assert env.get('/api/conversations/'+cid).json()['visual']['title']=='Revised outline'
    response=env.delete(f'/api/conversations/{cid}/visuals/{latest["id"]}')
    assert response.status_code==200 and response.json()['dismissed']
    assert env.get('/api/conversations/'+cid).json()['visual'] is None
    assert len(env.jarvis.state.store.rows('SELECT * FROM conversation_visuals WHERE conversation_id=?',(cid,)))==2


def test_visual_dismissal_is_scoped_to_its_conversation(env):
    cid,events=chat(env,[reply(None,('show_visual',day_visual())),reply('Here is your outline, sir.')],text='Show my day')
    visual=next(event['visual'] for event in events if event['type']=='visual')
    other=env.post('/api/conversations').json()['id']
    assert env.get('/api/conversations/'+other).json()['visual'] is None
    assert env.delete(f'/api/conversations/{other}/visuals/{visual["id"]}').status_code==400
    assert env.get('/api/conversations/'+cid).json()['visual']['id']==visual['id']
    assert env.delete('/api/conversations/'+cid).status_code==200
    assert env.jarvis.state.store.rows('SELECT * FROM conversation_visuals WHERE conversation_id=?',(cid,))==[]


def test_invalid_chart_does_not_emit_a_visual_or_claim_success(env):
    cid,events=chat(env,[reply(None,('show_visual',{'kind':'bars','title':'Focus time','items':[{'label':'Writing'}]})),
                         reply('The chart is ready.')],text='Show a chart of my focus time')
    assert not any(event['type']=='visual' for event in events)
    assert final_text(events)!='The chart is ready.'
    assert env.get('/api/conversations/'+cid).json()['visual'] is None
    assert env.get('/api/activity').json()['workflows'][0]['status']=='failed'


def test_normal_conversation_does_not_create_a_visual_or_replay_a_saved_one(env):
    cid,events=chat(env,[reply('Good morning, sir.')],text='Good morning')
    assert not any(event['type']=='visual' for event in events)
    assert env.get('/api/conversations/'+cid).json()['visual'] is None


def test_visual_can_be_hidden_by_voice_request_without_a_button_or_an_extra_approval(env):
    cid,_=chat(env,[reply(None,('show_visual',day_visual())),reply('Here is your outline, sir.')],text='Show my day')
    for _ in range(2):
        _,events=chat(env,[reply(None,('hide_visual',{})),reply('Of course, sir.')],text='Hide that graphic',cid=cid)
        assert [event['visual'] for event in events if event['type']=='visual']==[None]
        assert not any(event['type']=='approval' for event in events)
        assert env.get('/api/conversations/'+cid).json()['visual'] is None


def test_dismiss_endpoint_requires_the_local_client_header(env):
    cid,events=chat(env,[reply(None,('show_visual',day_visual())),reply('Here is your outline, sir.')],text='Show my day')
    visual=next(event['visual'] for event in events if event['type']=='visual')
    del env.headers['X-Jarvis-Client']
    assert env.delete(f'/api/conversations/{cid}/visuals/{visual["id"]}').status_code==403
    assert env.get('/api/conversations/'+cid).json()['visual']['id']==visual['id']
