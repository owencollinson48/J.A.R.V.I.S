import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from jarvis.speech import Speech, SpeechError, speech_text

HTTP_CLIENT = httpx.AsyncClient


@pytest.fixture
def speech(monkeypatch):
    for key in ("JARVIS_SPEECH_API_KEY", "OPENAI_API_KEY", "JARVIS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = SimpleNamespace(values={"provider_url": "https://api.openai.com/v1", "speech_voice": "cedar"}, api_key="chat-secret")
    return Speech(settings)


def mock_service(monkeypatch, handler):
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return HTTP_CLIENT(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("jarvis.speech.httpx.AsyncClient", client)


def test_natural_voice_uses_fixed_official_endpoint_and_british_instructions(speech, monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://api.openai.com/v1/audio/speech"
        assert request.headers["authorization"] == "Bearer chat-secret"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o-mini-tts"
        assert body["voice"] == "cedar"
        assert body["input"] == "Good evening."
        assert "British English" in body["instructions"]
        assert "professional" in body["instructions"] and "restrained warmth" in body["instructions"]
        assert "do not add" in body["instructions"]
        assert body["speed"] == 0.95
        assert body["response_format"] == "mp3"
        return httpx.Response(200, content=b"ID3natural-speech", headers={"content-type": "audio/mpeg"})
    mock_service(monkeypatch, handler)
    assert asyncio.run(speech.synthesize("Good evening.", 0.95)) == b"ID3natural-speech"
    assert len(seen) == 1


@pytest.mark.parametrize("text,expected", [
    ("Your tasks:\n\n1. **Open Outlook**\n\n2) Read 3 messages\n\n3. Finish at 9:30",
     "Your tasks: Open Outlook; Read 3 messages; Finish at 9:30."),
    ("## Today\n\n- [ ] Take 1.5 mg at 9:30\n  with  200 ml water\n\n• Call `Task 2`\n+ Open [the report](https://example.com/report)\n\nThen\tfinish.&nbsp; Thank\u200Byou.",
     "Today. Take 1.5 mg at 9:30 with 200 ml water; Call Task 2; Open the report; Then finish. Thankyou."),
    ("Use C# and https://example.com/a_b#top; keep 2 * 3 = 6.",
     "Use C# and https://example.com/a_b#top; keep 2 * 3 = 6."),
    ("__Open__ https://example.com/a__b__c", "Open https://example.com/a__b__c"),
])
def test_spoken_lists_compact_visual_spacing_with_brief_pauses_and_preserve_content(speech, monkeypatch, text, expected):
    assert speech_text(text) == expected
    assert speech_text(expected) == expected
    def handler(request):
        body = json.loads(request.content)
        assert body["input"] == expected
        assert "brief conversational pause between items" in body["instructions"]
        assert "whitespace as ordinary word spacing" in body["instructions"]
        return httpx.Response(200, content=b"ID3compact-list", headers={"content-type": "audio/mpeg"})
    mock_service(monkeypatch, handler)
    assert asyncio.run(speech.synthesize(text)) == b"ID3compact-list"


def test_formatting_only_speech_is_rejected_before_the_network(speech, monkeypatch):
    mock_service(monkeypatch, lambda request: pytest.fail("Unexpected network request"))
    with pytest.raises(SpeechError, match="spoken words"):
        asyncio.run(speech.synthesize("\n\t&nbsp;\u200B\n---\n"))


def test_third_party_chat_key_is_never_sent_to_speech(speech, monkeypatch):
    for url in ("https://another-provider.example/v1", "https://api.openai.com.evil.example/v1", "http://api.openai.com/v1", "http://localhost:11434/v1"):
        speech.settings.values["provider_url"] = url
        assert not speech.available
        with pytest.raises(SpeechError, match="OpenAI speech API key"):
            asyncio.run(speech.synthesize("Hello"))
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-secret")
    assert speech.key == "openai-only-secret"
    monkeypatch.setenv("JARVIS_SPEECH_API_KEY", "dedicated-env-secret")
    assert speech.key == "dedicated-env-secret"
    speech.settings.values["speech_api_key"] = "dedicated-saved-secret"
    assert speech.key == "dedicated-saved-secret"
    assert not any("secret" in str(value) for value in speech.public().values())


@pytest.mark.parametrize("selected_voice", [None, "marin"])
def test_professional_rp_delivery_preserves_short_confirmation_voice_and_key_isolation(speech, monkeypatch, selected_voice):
    speech.settings.values.update(provider_url="https://another-provider.example/v1", speech_api_key="dedicated-speech-secret")
    speech.settings.values.pop("speech_voice")
    if selected_voice:
        speech.settings.values["speech_voice"] = selected_voice
    confirmation = "Outlook is now open for you, sir."
    def handler(request):
        assert request.headers["authorization"] == "Bearer dedicated-speech-secret"
        assert str(request.url) == Speech.ENDPOINT
        body = json.loads(request.content)
        assert body["input"] == confirmation
        assert body["voice"] == (selected_voice or "cedar")
        assert body["speed"] == 1
        instructions = body["instructions"]
        for direction in ("southern British English", "Received Pronunciation (RP)", "British vowels", "non-rhotic", "crisp, precise consonants", "lower male register", "restrained warmth", "brisk and conversational", "without exaggeration or caricature", "Read only the supplied words"):
            assert direction in instructions
        for credential in ("dedicated-speech-secret", "chat-secret"):
            assert credential not in request.content.decode()
            assert credential not in json.dumps(speech.public())
        return httpx.Response(200, content=b"ID3confirmation", headers={"content-type": "audio/mpeg"})
    mock_service(monkeypatch, handler)
    assert asyncio.run(speech.synthesize(confirmation)) == b"ID3confirmation"


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_service_errors_never_expose_key_input_or_provider_body(speech, monkeypatch, status):
    mock_service(monkeypatch, lambda request: httpx.Response(status, text="chat-secret PRIVATE SPOKEN CONTENT"))
    with pytest.raises(SpeechError) as caught:
        asyncio.run(speech.synthesize("PRIVATE SPOKEN CONTENT"))
    assert "chat-secret" not in str(caught.value)
    assert "PRIVATE SPOKEN CONTENT" not in str(caught.value)


@pytest.mark.parametrize("text,rate", [("", 1), ("x" * 4001, 1), ("hello", 1.5), ("hello", True), ("hello", float("nan"))])
def test_invalid_speech_is_rejected_before_network(speech, monkeypatch, text, rate):
    mock_service(monkeypatch, lambda request: pytest.fail("Unexpected network request"))
    with pytest.raises(SpeechError):
        asyncio.run(speech.synthesize(text, rate))


@pytest.mark.parametrize("content,kind", [(b"", "audio/mpeg"), (b"not audio", "text/html"), (b"x" * (Speech.MAX_AUDIO + 1), "audio/mpeg")])
def test_empty_unexpected_or_unbounded_audio_is_rejected(speech, monkeypatch, content, kind):
    mock_service(monkeypatch, lambda request: httpx.Response(200, content=content, headers={"content-type": kind}))
    with pytest.raises(SpeechError):
        asyncio.run(speech.synthesize("Hello"))


def test_network_errors_are_safe_and_cancellation_is_not_swallowed(speech, monkeypatch):
    def failed(request):
        raise httpx.ConnectError("chat-secret PRIVATE SPOKEN CONTENT", request=request)
    mock_service(monkeypatch, failed)
    with pytest.raises(SpeechError, match="could not be reached") as caught:
        asyncio.run(speech.synthesize("PRIVATE SPOKEN CONTENT"))
    assert "secret" not in str(caught.value)
    async def cancelled(request):
        raise asyncio.CancelledError()
    mock_service(monkeypatch, cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(speech.synthesize("Hello"))
