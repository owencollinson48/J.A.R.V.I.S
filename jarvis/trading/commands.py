"""Small local stop recognizer; flexible requests otherwise use model tools."""
import re


def normalized(text):
    return text.replace('’',"'").replace('‘',"'") if isinstance(text,str) else ''


def not_authorization(text):
    return bool(re.search(r"\b(?:don't|do not|dont|not|never|without|can't|cannot|shouldn't|should not|explain|example|recommend|advice|what if)\b",text,re.I) or
                re.search(r"\b(?:should I|would I|could I|how (?:do|can|could|should) (?:I|you)|what happens|tell me (?:how|whether))\b",text,re.I))


def approves_reset(text):
    text=normalized(text)
    return bool(text and not not_authorization(text) and
        re.search(r'\b(?:reset|clear|release)\b',text,re.I) and
        re.search(r'\b(?:emergency|trading|stop|latch)\b',text,re.I))


def stop_intent(text):
    if not isinstance(text,str): return None
    value=normalized(text).lower().strip()
    value=re.sub(r"^(?:hey\s+)?(?:jarvis[, ]+)?(?:(?:please|can you|could you|would you|i want you to)\s+)*",'',value)
    if not_authorization(value) or re.search(r'\b(?:why|if|when|unless|until|after|quote)\b',value) or any(c in value for c in '"“”'): return None
    if re.match(r'^(?:emergency\s+)?(?:stop|halt|pause|disable|turn off|shut down)\s+(?:(?:all|my|the|automated|paper|new|NQ)\s+)*(?:trading|trades|automated entries)\b',value,re.I):
        if re.search(r'\b(?:emergency|immediately|everything)\b',value): return 'emergency'
        if re.search(r'\b(?:today|rest of (?:the )?day|for the day)\b',value): return 'pause_today'
        return 'off'
    return None


def execution_request(text):
    if not isinstance(text,str): return False
    return bool(re.search(r'\b(?:trading|trades?|futures|nasdaq|NQ|TradingView|paper (?:positions?|orders?|account))\b',text,re.I) and
                re.search(r'\b(?:start|stop|buy|sell|execute|submit|place|close|trade|trading|strategy|run)\b',text,re.I))


def approves_strategy(text):
    if not isinstance(text,str): return False
    text=normalized(text)
    if not_authorization(text): return False
    return bool(re.search(r'\b(?:approve|activate|switch|replace|use|apply)\b',text,re.I) and
                re.search(r'\b(?:strategy|version|V\d+)\b',text,re.I))
