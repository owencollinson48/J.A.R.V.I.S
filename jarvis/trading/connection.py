"""Observe the installed TradingView app without treating a title as account proof.

No browser, undocumented API, live broker, or unverified order transport is used.
Native order submission remains disabled until its form and reconciliation have
been validated against the installed application. An image alone is not an
account-verification assertion the language model can set.
"""
import copy
import platform
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from ..execution import Cancelled
from ..store import now


class TradingViewDesktop:
    def __init__(self, applications, desktop):
        self.applications, self.desktop = applications, desktop
        self.state = self._empty('Connect the installed TradingView application to inspect its selected account.')

    @staticmethod
    def _empty(reason):
        return {'connected':False,'verified':False,'orders_enabled':False,'reason':reason,
                'application':'TradingView Desktop','observed_at':None,'window_id':None,
                'account':None,'orders':None,'positions':None}

    @staticmethod
    def native_window(window):
        # A website/title mentioning TradingView is not the installed application.
        process = str(window.get('process','')).lower()
        return process == 'tradingview.exe'

    def selected_paper_account(self, window_id, observation):
        """Read selected UIA state; an unselected account-menu option is not proof."""
        _, wrapper = self.desktop.window(window_id)
        for control in wrapper.descendants(depth=6):
            try:
                if not control.is_visible(): continue
                info = control.element_info
                label = str(info.name or '').strip()
                if not re.fullmatch(r'Paper Trading(?:\s*(?:by TradingView|account))?',label,re.I): continue
                if bool(control.iface_selection_item.CurrentIsSelected): return label
            except Exception:
                continue
        # Some builds expose an account selector with an actual selected value.
        for item in observation.get('elements',[]):
            if (item.get('type')=='ComboBox' and
                    re.search(r'\b(?:account|broker)\b',item.get('name',''),re.I) and
                    re.fullmatch(r'Paper Trading(?:\s+account)?',item.get('text','').strip(),re.I)):
                return item['text'].strip()
        return None

    @staticmethod
    def balances(observation):
        fields = {}
        currencies=set()
        for item in observation.get('elements',[]):
            label = item.get('name','').strip()
            currencies.update(re.findall(r'\b(?:USD|EUR|GBP|AUD|CAD|JPY|CHF)\b',label+' '+str(item.get('text',''))))
            for key, pattern in (('balance',r'Balance'),('equity',r'(?:Equity|Account equity)'),
                                 ('available_funds',r'(?:Available funds|Available balance|Buying power)')):
                match = re.fullmatch(pattern+r'\s*[:\n]?\s*(?:USD\s*|\$)?([\d,]+(?:\.\d+)?)\s*(?:USD)?',label,re.I)
                value = match.group(1) if match else item.get('text') if re.fullmatch(pattern,label,re.I) else None
                if value is not None:
                    try:
                        amount = Decimal(str(value).replace(',','').replace('$','').replace('USD','').strip())
                        if amount.is_finite() and amount>=0: fields[key]=str(amount)
                    except (ValueError,InvalidOperation): pass
        return {**fields,'currency':next(iter(currencies)) if len(currencies)==1 else None} if len(fields)==3 else None

    def connect(self, cancel=None):
        if platform.system()!='Windows':
            self.state=self._empty('TradingView Desktop inspection must run in JARVIS on your Windows PC. The cloud cannot access the installed application.')
            return self.public()
        try:
            result=self.applications.launch('TradingView',cancel)
            if not result.get('verified') or not result.get('window_id'):
                self.state=self._empty('The installed TradingView application did not produce a verified visible window.')
                return self.public()
            return self.observe(result['window_id'],cancel)
        except Cancelled: raise
        except Exception as exc:
            self.state=self._empty('TradingView Desktop connection failed: '+str(exc)[:600])
            return self.public()

    def observe(self, window_id=None, cancel=None):
        window_id=window_id or self.state.get('window_id')
        if not window_id: return self.public()
        state=self._empty('The selected account could not be verified as Paper Trading. Open the Trading Panel and select your Paper Trading account in the installed app, then check again.')
        state.update(window_id=window_id,observed_at=now())
        try:
            if cancel: cancel.check()
            observation=self.desktop.inspect(window_id,cancel)
            window=observation.get('window',{})
            if not self.native_window(window):
                state['reason']='The observed window is not the installed TradingView executable. No account or order was accepted.'
            else:
                state['application_visible']=True
                state['window_identity']={key:window.get(key) for key in ('id','pid','process')}
                selected=self.selected_paper_account(window_id,observation)
                account=self.balances(observation)
                state['selected_account']=selected
                state['account']=account if selected else None
                if selected and account:
                    state.update(connected=True,verified=True,
                        reason='Paper Trading selection and account values were observed in TradingView Desktop. Native order-form mapping, bracket orders and fill reconciliation have not been validated; automated desktop orders remain disabled.')
                try:
                    state['screenshot']=self.desktop.screenshot(window_id,cancel)
                except Cancelled: raise
                except Exception as exc: state['capture_error']=str(exc)[:500]
        except Cancelled: raise
        except Exception as exc: state['reason']='TradingView Desktop inspection failed: '+str(exc)[:600]
        self.state=state
        return self.public()

    def public(self):
        state=copy.deepcopy(self.state)
        if state.get('verified') and state.get('observed_at'):
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(state['observed_at'])).total_seconds()
            if age>30:
                state.update(connected=False,verified=False,account=None,
                    reason='The TradingView Desktop observation has expired. Check the installed application again; no current account connection is assumed.')
        return state


def public_status(adapter='tradingview_desktop', desktop=None):
    observed=desktop.public() if desktop else TradingViewDesktop._empty('TradingView Desktop has not been inspected in this JARVIS session.')
    return {'adapter':adapter,'label':'TradingView Desktop' if adapter=='tradingview_desktop' else 'JARVIS independent paper simulator',
            'simulated':True,'live_broker_supported':False,'tradingview':observed,
            'data_requirement':'Supply licensed completed NQ candles and synchronized ES candles for SMT. Historical imports are never a live feed.',
            'webhook_note':'JARVIS keeps its authenticated loopback-only API; no public TradingView webhook is enabled.'}
