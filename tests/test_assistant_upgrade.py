"""Persist model/browser/speech upgrade defaults without altering custom providers."""
import json
import pytest
from jarvis.config import Settings

def test_existing_openai_mini_upgrades_once_and_keeps_key(tmp_path):
    private=tmp_path/'private';private.mkdir();path=private/'settings.json'
    path.write_text(json.dumps({'provider_url':'https://api.openai.com/v1','model':'gpt-4.1-mini','api_key':'synthetic-existing-key'}))
    settings=Settings(private)
    assert settings.values['model']=='gpt-6.1-sol'
    assert settings.values['browser_mode']=='existing' and settings.values['speech_mode']=='auto'
    assert settings.api_key=='synthetic-existing-key'
    settings.update({'model':'gpt-6-astra','browser_mode':'managed','speech_mode':'browser'})
    reopened=Settings(private)
    assert reopened.values['model']=='gpt-6-astra' and reopened.values['browser_mode']=='managed'
    assert reopened.values['speech_mode']=='browser'

@pytest.mark.parametrize('provider,model',[('http://localhost:11434/v1','qwen3:8b'),('https://example.com/v1','custom-model')])
def test_upgrade_keeps_local_and_custom_model_choices(tmp_path,provider,model):
    private=tmp_path/'private';private.mkdir()
    (private/'settings.json').write_text(json.dumps({'provider_url':provider,'model':model}))
    settings=Settings(private)
    assert settings.values['model']==model

def test_public_settings_expose_readiness_but_never_speech_key(tmp_path):
    settings=Settings(tmp_path/'private')
    settings.update({'speech_api_key':'synthetic-speech-key','speech_voice':'marin'})
    public=settings.public()
    assert public['speech_available'] and public['speech_voice']=='marin'
    assert public['has_speech_api_key']
    assert 'speech_api_key' not in public and 'assistant_upgrade_version' not in public
    assert 'synthetic-speech-key' not in json.dumps(public)

@pytest.mark.parametrize('key,value',[('speech_mode','random'),('speech_voice','unknown'),('browser_mode','copy-profile'),('speech_api_key',True),('background_voice','always')])
def test_settings_reject_unknown_voice_and_browser_modes(tmp_path,key,value):
    with pytest.raises(ValueError): Settings(tmp_path/'private').update({key:value})
