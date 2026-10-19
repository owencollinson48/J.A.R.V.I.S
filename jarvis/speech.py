"""Optional natural speech, sent only to OpenAI's fixed speech endpoint."""

import asyncio
import math
import os
import re
from urllib.parse import urlparse

import httpx


def speech_text(text):
    """Turn visual list spacing into short spoken phrase boundaries."""
    text = re.sub(r"```[\s\S]*?```", "The code is in our chat.", text)
    text = re.sub(r"<br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"&nbsp;|&#160;|&#xA0;", " ", text, flags=re.I)
    text = re.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(^|\s)__([^_\n]+)__(?=\s|[.,!?;:]|$)", r"\1\2", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(^|\s)\*([^*\n]+)\*(?=\s|[.,!?;:]|$)", r"\1\2", text)
    marker = r"^(?:[-*+\u2022\u2023\u25E6\u25AA\u25CF]|\d{1,3}[.)])\s+"
    phrases = []
    paragraph = False
    for line in text.split("\n"):
        value = line.strip()
        if not value or re.fullmatch(r"(?:[-*_]\s*){3,}", value):
            paragraph = True
            continue
        indent = len(line) - len(line.lstrip())
        listed = bool(re.match(marker, value))
        value = re.sub(r"^#{1,6}\s+", "", value)
        value = re.sub(marker, "", value)
        value = re.sub(r"^\[[ xX]\]\s+", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            continue
        previous = phrases[-1] if phrases else None
        if previous and previous["list"] and not listed and not paragraph and indent > previous["indent"]:
            previous["text"] += " " + value
        else:
            if previous and (previous["list"] or listed or paragraph) and not re.search(r"[.!?;:\u2026]$", previous["text"]):
                previous["text"] += ";" if previous["list"] else "."
            phrases.append({"text": value, "list": listed, "indent": indent})
        paragraph = False
    if phrases and phrases[-1]["list"] and not re.search(r"[.!?;:\u2026]$", phrases[-1]["text"]):
        phrases[-1]["text"] += "."
    return " ".join(phrase["text"] for phrase in phrases)


class SpeechError(Exception):
    """An error safe to show without leaking credentials or spoken text."""


class Speech:
    ENDPOINT = "https://api.openai.com/v1/audio/speech"
    MAX_TEXT = 4000
    MAX_AUDIO = 10 * 1024 * 1024
    VOICES = {"cedar", "marin"}
    INSTRUCTIONS = (
        "Deliver the supplied words as an original professional British personal assistant. "
        "Use a clearly cultivated southern British English Received Pronunciation (RP) accent: "
        "distinctly British vowels, natural non-rhotic r pronunciation and crisp, precise consonants. "
        "Use a composed, softly resonant lower male register, quietly confident, attentive and discreet, with restrained warmth. "
        "Keep the delivery brisk and conversational: smooth connected phrasing, a purposeful pace and short natural pauses. "
        "For lists, use only a brief conversational pause between items; never insert a long dramatic silence. "
        "Treat whitespace as ordinary word spacing, and do not read formatting or list markers aloud. "
        "Make the accent unmistakably British without exaggeration or caricature. "
        "Avoid exaggerated drama, breathiness, sing-song intonation, drawn-out syllables and robotic pauses. "
        "Read only the supplied words; do not add greetings, filler or commentary."
    )

    def __init__(self, settings):
        self.settings = settings

    @property
    def key(self):
        # A third-party chat provider's key must never be sent to OpenAI.
        dedicated = self.settings.values.get("speech_api_key") or os.getenv("JARVIS_SPEECH_API_KEY")
        if dedicated:
            return dedicated
        url = urlparse(self.settings.values.get("provider_url", ""))
        try:
            official = url.scheme == "https" and url.hostname == "api.openai.com" and url.port in {None, 443} and not (url.username or url.password)
        except ValueError:
            official = False
        if official:
            key = self.settings.api_key
            if key:
                return key
        return os.getenv("OPENAI_API_KEY", "")

    @property
    def available(self):
        return bool(self.key)

    def public(self):
        return {"speech_available": self.available,
                "speech_mode": self.settings.values.get("speech_mode", "auto"),
                "speech_voice": self.settings.values.get("speech_voice", "cedar")}

    async def synthesize(self, text, rate=1):
        if not isinstance(text, str) or not text.strip() or len(text) > self.MAX_TEXT:
            raise SpeechError(f"Speech text must contain between 1 and {self.MAX_TEXT} characters.")
        text = speech_text(text)
        if not text:
            raise SpeechError("Speech text must contain spoken words.")
        if len(text) > self.MAX_TEXT:
            raise SpeechError(f"Speech text must contain between 1 and {self.MAX_TEXT} characters.")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0.75 <= rate <= 1.25:
            raise SpeechError("Choose a speaking speed between 0.75 and 1.25.")
        key = self.key
        if not key:
            raise SpeechError("Add an OpenAI speech API key in Settings to use the natural British voice.")
        voice = self.settings.values.get("speech_voice", "cedar")
        if voice not in self.VOICES:
            raise SpeechError("Choose Cedar or Marin for the natural voice.")
        body = {"model": "gpt-4o-mini-tts", "voice": voice, "input": text,
                "instructions": self.INSTRUCTIONS, "response_format": "mp3", "speed": rate}
        try:
            async with asyncio.timeout(45):
                async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
                    async with client.stream("POST", self.ENDPOINT, headers={"Authorization": f"Bearer {key}"}, json=body) as response:
                        if response.status_code != 200:
                            hints = {401: "The natural voice API key was rejected.",
                                     403: "The natural voice is unavailable for this OpenAI account.",
                                     429: "The natural voice reached an OpenAI rate or billing limit."}
                            raise SpeechError(hints.get(response.status_code, "The natural voice service could not generate speech."))
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if content_type not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                            raise SpeechError("The natural voice service returned an unexpected audio response.")
                        audio = bytearray()
                        async for part in response.aiter_bytes():
                            if len(audio) + len(part) > self.MAX_AUDIO:
                                raise SpeechError("The generated audio exceeded the playback size limit.")
                            audio.extend(part)
                        if not audio:
                            raise SpeechError("The natural voice service returned no audio.")
                        return bytes(audio)
        except (httpx.HTTPError, TimeoutError):
            raise SpeechError("The natural voice service could not be reached. Browser speech is still available.") from None
