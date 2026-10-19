from fastapi.testclient import TestClient
from jarvis.app import create_app
from jarvis.speech import Speech,SpeechError


def test_speech_endpoint_requires_local_session_and_client_header_and_returns_private_audio(tmp_path,monkeypatch):
    captured=[]
    async def synthesize(self,text,rate=1): captured.append((text,rate));return b'ID3-fixture-audio'
    monkeypatch.setattr(Speech,'synthesize',synthesize)
    app=create_app(tmp_path/'private')
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.post('/api/speech',json={'text':'Hello'}).status_code==401
        response=client.get('/')
        assert "media-src 'self' blob:" in response.headers['content-security-policy']
        assert client.post('/api/speech',json={'text':'Hello'}).status_code==403
        response=client.post('/api/speech',json={'text':'Good evening.', 'rate':.95},headers={'X-Jarvis-Client':'dashboard'})
        assert response.status_code==200,response.text
        assert response.content==b'ID3-fixture-audio'
        assert response.headers['content-type']=='audio/mpeg' and response.headers['cache-control']=='no-store'
        assert captured==[('Good evening.',.95)]
        assert not list((tmp_path/'private').glob('*.mp3'))


def test_speech_endpoint_has_bounded_input_and_safe_service_failure(tmp_path,monkeypatch):
    async def fail(self,text,rate=1): raise SpeechError('The natural voice API key was rejected.')
    monkeypatch.setattr(Speech,'synthesize',fail)
    with TestClient(create_app(tmp_path/'private'),base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        assert client.post('/api/speech',json={'text':'x'*4001}).status_code==422
        assert client.post('/api/speech',json={'text':'hello','rate':True}).status_code==422
        response=client.post('/api/speech',json={'text':'hello'})
        assert response.status_code==502 and 'API key was rejected' in response.json()['detail']
