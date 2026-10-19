"""Verify a provider/model before saving a replacement; never execute local tools."""
import asyncio
import copy
import re

from .provider import ProviderError

PREFERRED = ('gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-5.6-sol','gpt-5.5','gpt-5.4',
             'gpt-5.2','gpt-4.1','gpt-4o','gpt-5.4-mini','gpt-4.1-mini','gpt-4o-mini')


def candidate_models(identifiers, current):
    def general(identifier):
        return not re.search(r'embed|tts|audio|realtime|transcrib|whisper|image|dall-e|moderation|search-preview',identifier,re.I)
    offered = {name for name in identifiers if name != current and general(name)}
    ordered = [name for name in PREFERRED if name in offered]
    ordered.extend(sorted(offered-set(ordered)))
    # Include an established general tool model in the bounded probe list rather
    # than spending every attempt on variants of the same incompatible family.
    selected = ordered[:3]
    baseline = next((name for name in ('gpt-4.1','gpt-4o') if name in offered),None)
    if baseline and baseline not in selected and len(selected)==3:
        selected[-1] = baseline
    return selected


async def repair_connection(settings, provider_factory, busy=lambda:False):
    if busy(): raise ProviderError('Finish or cancel the active task before checking another model.')
    snapshot = (settings.values['provider_url'],settings.values['model'],settings.api_key)
    current = snapshot[1]
    frozen_settings = copy.copy(settings)
    frozen_settings.values = {**settings.values,'api_key':snapshot[2]}
    provider = provider_factory(frozen_settings)
    def validate_snapshot():
        if busy(): raise ProviderError('A task started during model discovery; no model was changed.')
        if snapshot != (settings.values['provider_url'],settings.values['model'],settings.api_key):
            raise ProviderError('Connection settings changed during the check. Retry with the saved settings; no model was changed by discovery.')
    original = None
    try:
        async with asyncio.timeout(25):
            result = await provider.check()
    except TimeoutError:
        original = ProviderError('The current model did not complete the tool connection check within 25 seconds.')
    except ProviderError as exc:
        if exc.status_code in {401,429} or (not frozen_settings.api_key and not frozen_settings.local_provider): raise
        original = exc
    else:
        validate_snapshot()
        return {**result,'changed':False,'previous_model':current}
    validate_snapshot()
    try:
        async with asyncio.timeout(15): identifiers = await provider.models()
    except (ProviderError,TimeoutError) as exc:
        detail = str(exc) if isinstance(exc,ProviderError) else 'Model listing timed out.'
        raise ProviderError(f'{original} Available models could not be checked: {detail} Select a tool-capable model supplied by this provider. No model was changed.') from None
    attempts = []
    candidates = candidate_models(identifiers,current)
    try:
        async with asyncio.timeout(60):
            for candidate in candidates:
                if busy(): raise ProviderError('A task started during model discovery. Cancel or finish it before changing models.')
                probe_settings = copy.copy(frozen_settings)
                probe_settings.values = {**frozen_settings.values,'model':candidate}
                try:
                    async with asyncio.timeout(20): result = await provider_factory(probe_settings).check()
                except TimeoutError:
                    attempts.append(f'{candidate}: tool check timed out')
                    continue
                except ProviderError as exc:
                    if exc.status_code in {401,429}: raise
                    attempts.append(f'{candidate}: {str(exc)[:650]}')
                    continue
                validate_snapshot()
                settings.update({'model':candidate})
                return {**result,'ok':True,'model':candidate,'previous_model':current,'changed':True,
                        'message':f'Connected with {candidate}. JARVIS tool schemas, streaming and tool-result continuation passed. The selected model was saved; local actions were not run by this check.'}
    except TimeoutError:
        attempts.append('Connection repair reached its time limit')
    details = ' '.join(attempts) if attempts else 'No other suitable models were listed by this provider.'
    raise ProviderError(f'No compatible model was verified. Current model {current}: {original} {details} No model was changed.')
