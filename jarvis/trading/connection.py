"""Observed, bounded connection steps for the installed TradingView application.

Only Trading Panel, Paper Trading and a Paper-scoped Connect control can be
clicked here. This adapter contains no order submission or broker transport.
"""
import copy
import ntpath
import platform
import re
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from ..execution import Cancelled
from ..store import now

PAPER = re.compile(r'Paper\s+Trading(?:\s+(?:by\s+TradingView|account))?',re.I)
PAPER_CONTAINER = re.compile(r'Paper\s+Trading(?:\s+by\s+TradingView)?',re.I)
CURRENCIES = r'USD|EUR|GBP|AUD|CAD|JPY|CHF'


class TradingViewDesktop:
    def __init__(self, applications, desktop):
        self.applications, self.desktop = applications, desktop
        self.lock=threading.RLock()
        self.work_lock=threading.Lock()
        self.trace=[]
        self.selection_errors=[]
        self.state=self._empty('Connect the installed TradingView application to inspect its selected account.')

    @staticmethod
    def _empty(reason,stage='not_checked'):
        return {'connected':False,'verified':False,'orders_enabled':False,'reason':reason,'stage':stage,
                'application':'TradingView Desktop','application_visible':False,'observed_at':None,
                'window_id':None,'account':None,'orders':None,'positions':None,
                'diagnostics':{'checks':[],'actions':[],'controls':[]}}

    def _save(self,state):
        with self.lock: self.state=copy.deepcopy(state)
        return self.public()

    @staticmethod
    def native_window(window):
        return ntpath.basename(str(window.get('process',''))).lower()=='tradingview.exe'

    def selected_paper_account(self,window_id,observation):
        self.selection_errors=[]
        try:
            _,wrapper=self.desktop.window(window_id)
            for control in wrapper.descendants(depth=6):
                try:
                    if not control.is_visible(): continue
                    info=control.element_info
                    if info.control_type=='Button' and str(info.name or '').strip().lower()=='disconnect' and self._paper_ancestor(control):
                        return 'Paper Trading'
                    if (info.control_type not in {'TabItem','ComboBox'} or
                            not PAPER.fullmatch(str(info.name or '').strip())): continue
                    if bool(control.iface_selection_item.CurrentIsSelected): return str(info.name).strip()
                except Exception: continue
        except Cancelled: raise
        except Exception as exc: self.selection_errors.append(type(exc).__name__+': '+str(exc)[:400])
        # A usable observation must survive failure of a second wrapper query.
        for item in observation.get('elements',[]):
            if (item.get('type')=='ComboBox' and
                    re.search(r'\b(?:account|broker)\b',item.get('name',''),re.I) and
                    PAPER.fullmatch(item.get('text','').strip())):
                return item['text'].strip()
        return None

    @staticmethod
    def _paper_ancestor(control):
        parent=control.parent()
        for _ in range(6):
            if parent is None: break
            data=parent.element_info
            if data.control_type in {'Window','Dialog','Pane','Group'} and PAPER_CONTAINER.fullmatch(str(data.name or '').strip()): return True
            if data.control_type=='Window': break
            parent=parent.parent()
        return False

    @staticmethod
    def balances(observation):
        fields,currencies={},set()
        for item in observation.get('elements',[]):
            if item.get('password'): continue
            label=str(item.get('name','')).strip()
            currencies.update(re.findall(r'\b(?:'+CURRENCIES+r')\b',label+' '+str(item.get('text',''))))
            for key,pattern in (('balance',r'(?:Balance|Account balance)'),('equity',r'(?:Equity|Account equity)'),
                                ('available_funds',r'(?:Available funds|Available balance|Buying power|Free margin|Available margin)')):
                match=re.fullmatch(pattern+r'\s*[:\n]?\s*(?:(?:'+CURRENCIES+r')\s*|\$)?(-?[\d,]+(?:\.\d+)?)\s*(?:'+CURRENCIES+r')?',label,re.I)
                value=match.group(1) if match else item.get('text') if re.fullmatch(pattern,label,re.I) else None
                if value is not None:
                    try:
                        clean=re.sub(r'\b(?:'+CURRENCIES+r')\b','',str(value),flags=re.I).replace(',','').replace('$','').strip()
                        amount=Decimal(clean)
                        if amount.is_finite(): fields[key]=str(amount)
                    except (ValueError,InvalidOperation): pass
        return {**fields,'currency':next(iter(currencies)) if len(currencies)==1 else None} if fields else None

    @staticmethod
    def _controls(observation):
        result=[]
        for item in observation.get('elements',[])[:250]:
            if item.get('password'): continue
            name=str(item.get('name',''))[:180]
            if re.search(r'password|secret|token|api.?key|credential',name,re.I): continue
            entry={'name':name,'type':str(item.get('type',''))[:60]}
            if item.get('text') and (item.get('type')=='ComboBox' or re.search(r'\b(?:balance|equity|funds|margin)\b',name,re.I)):
                entry['text']=str(item['text'])[:240]
            result.append(entry)
        return result

    def _capture(self,state,cancel):
        if not state.get('application_visible'): return
        try:
            saved=self.desktop.screenshot(state['window_id'],cancel)
            state['screenshot']=saved
            state.update({k:saved[k] for k in ('image_id','window_id','width','height','original_width','original_height','capture_offset') if k in saved})
            state['diagnostics']['checks'].append({'name':'Window screenshot','status':'passed','detail':'Captured the actual TradingView window.'})
        except Cancelled: raise
        except Exception as exc:
            state['capture_error']=str(exc)[:600]
            state['diagnostics']['checks'].append({'name':'Window screenshot','status':'failed','detail':state['capture_error']})

    def observe(self,window_id=None,cancel=None,capture=True):
        window_id=window_id or self.state.get('window_id')
        if not window_id: return self.public()
        state=self._empty('The selected account could not be verified as Paper Trading. Open the Trading Panel and select your Paper Trading account in the installed app, then check again.','paper_selection_required')
        state.update(window_id=window_id,observed_at=now())
        checks=state['diagnostics']['checks']
        observation={}
        try:
            if cancel: cancel.check()
            try: observation=self.desktop.inspect(window_id,cancel)
            except Cancelled: raise
            except Exception as exc:
                state['inspection_error']=type(exc).__name__+': '+str(exc)[:600]
                reader=getattr(self.desktop,'native_window',None)
                if reader: observation={'window':reader(window_id),'elements':[],
                    'accessibility':{'available':False,'error':state['inspection_error']}}
                else: raise
            window=observation.get('window',{})
            if not self.native_window(window):
                state.update(stage='wrong_application',reason='The observed window is not the installed TradingView executable. No account or connection input was accepted.')
                checks.append({'name':'Installed application identity','status':'failed','detail':str(window.get('process','unknown'))})
            else:
                state['application_visible']=True
                state['window_identity']={key:window.get(key) for key in ('id','pid','process')}
                checks.append({'name':'Installed application identity','status':'passed','detail':'A visible TradingView.exe window was observed.'})
                accessibility=observation.get('accessibility',{})
                state['diagnostics']['controls']=self._controls(observation)
                available=accessibility.get('available') is not False and bool(observation.get('elements'))
                checks.append({'name':'Accessible controls','status':'passed' if available else 'failed',
                    'detail':str(accessibility.get('error') or state.get('inspection_error') or
                                 (str(len(observation.get('elements',[])))+' visible controls read.'))[:700]})
                selected=self.selected_paper_account(window_id,observation) if available else None
                account=self.balances(observation) if available else None
                state['selected_account']=selected
                state['account']=account if selected else None
                checks.append({'name':'Selected Paper Trading account','status':'passed' if selected else 'unknown',
                               'detail':selected or 'No explicit current Paper Trading account selection was readable.'})
                known=[key for key in ('balance','equity','available_funds') if account and key in account]
                checks.append({'name':'Account amounts','status':'passed' if selected and known else 'unknown',
                               'detail':', '.join(known)+' observed; missing amounts remain unknown.' if selected and known else 'Account amounts were not readable in this view.'})
                if self.selection_errors: state['diagnostics']['selection_errors']=self.selection_errors[:3]
                if selected and account:
                    state.update(connected=True,verified=True,stage='paper_verified',
                        reason='Paper Trading selection and account values were observed in TradingView Desktop. Native order-form mapping, bracket orders and fill reconciliation have not been validated; automated desktop orders remain disabled.')
                elif selected:
                    state.update(stage='account_data_unreadable',reason='TradingView is open and Paper Trading is selected, but its account summary is not readable. Expand the Paper Trading account summary, then check again. Desktop orders remain disabled.')
                elif not available:
                    state.update(stage='accessibility_unavailable',reason='TradingView is open, but Windows did not expose usable account controls. The connection report and screenshot show this inspection result; no connection or order clicks were guessed.')
                if observation.get('limited'): state['diagnostics']['limited']=True
        except Cancelled: raise
        except Exception as exc:
            state.update(stage='connection_error',reason='TradingView Desktop inspection failed: '+str(exc)[:600])
        state['diagnostics']['actions']=copy.deepcopy(self.trace)
        if capture: self._capture(state,cancel)
        self._observation=observation
        return self._save(state)

    def connect_scope(self,window_id,element_id):
        """A generic Connect button is insufficient; require its Paper container."""
        try:
            item,_,control,info=self.desktop.target(window_id,element_id)
            if not self.native_window(item) or info.get('type')!='Button' or info.get('name','').strip().lower()!='connect': return False
            return self._paper_ancestor(control)
        except Cancelled: raise
        except Exception: pass
        return False

    def _candidate(self,window_id,observation,attempted):
        values=observation.get('elements',[])
        candidates=[c for c in values if c.get('id') and not c.get('password')]
        # Prefer a scoped Paper connect action or explicit Paper control before
        # toggling a Trading Panel tab that may already be open.
        for name in ('Connect','Paper Trading','Trading Panel'):
            if name in attempted: continue
            matches=[]
            for c in candidates:
                label=str(c.get('name','')).strip()
                if name=='Connect':
                    accepted=label.lower()=='connect' and c.get('type')=='Button' and self.connect_scope(window_id,c['id'])
                elif name=='Paper Trading': accepted=bool(PAPER.fullmatch(label)) and c.get('type') in {'Button','MenuItem','TabItem'}
                else: accepted=label.lower()=='trading panel' and c.get('type') in {'Button','TabItem'}
                if accepted: matches.append(c)
            if len(matches)==1: return name,matches[0]
        return None

    def _navigate(self,window_id,cancel):
        attempted=set()
        state=self.observe(window_id,cancel,capture=False)
        for _ in range(4):
            if state.get('verified') or state['stage'] in {'wrong_application','accessibility_unavailable','connection_error'}: break
            choice=self._candidate(window_id,self._observation,attempted)
            if not choice: break
            name,control=choice;attempted.add(name)
            args={'action':'click','window_id':window_id,'element_id':control['id']}
            try:
                if cancel: cancel.check()
                preview=self.desktop.preflight(args)
                if name=='Connect' and not self.connect_scope(window_id,control['id']):
                    raise ValueError('The Connect control no longer belongs to the Paper Trading panel.')
                if preview.get('password') or preview.get('interpreter'): raise ValueError('The connection target changed to an unrelated control.')
                receipt=self.desktop.act(args,preview,cancel)
                self.trace.append({'name':name,'status':'input_sent','detail':'Delivered one observed connection-control click; reading the resulting state.'})
                if isinstance(receipt,dict) and (receipt.get('error') or receipt.get('observation_error')):
                    state=self.observe(window_id,cancel,capture=False)
                    if not state.get('verified'):
                        state.update(stage='connection_error',reason='The '+name+' input was delivered, but its result could not be verified: '+str(receipt.get('error') or receipt.get('observation_error'))[:400]+'. No input was repeated.')
                    break
            except Cancelled: raise
            except Exception as exc:
                self.trace.append({'name':name,'status':'stopped','detail':str(exc)[:500]+'. No input was replayed.'})
                state=self.observe(window_id,cancel,capture=False)
                state['reason']='Connection stopped after the '+name+' step: '+str(exc)[:400]+'. Check the report; no click was repeated.'
                state['stage']='connection_error'
                break
            deadline=time.monotonic()+1.5
            while True:
                state=self.observe(window_id,cancel,capture=False)
                if state.get('verified') or self._candidate(window_id,self._observation,attempted) or state['stage'] in {'accessibility_unavailable','connection_error','wrong_application'} or time.monotonic()>=deadline: break
                if cancel:
                    cancel.check();cancel.event.wait(.1)
                else: time.sleep(.1)
        state['diagnostics']['actions']=copy.deepcopy(self.trace)
        self._capture(state,cancel)
        return self._save(state)

    def connect(self,cancel=None):
        if not self.work_lock.acquire(blocking=False): return {**self.public(),'message':'A TradingView connection check is already running.'}
        self.trace=[]
        try:
            if platform.system()!='Windows':
                return self._save(self._empty('TradingView Desktop inspection must run in JARVIS on your Windows PC. The cloud cannot access the installed application.','desktop_unavailable'))
            adapter=getattr(self.applications,'adapter',None)
            windows=adapter.windows() if adapter and getattr(adapter,'supported',True) and callable(getattr(adapter,'windows',None)) else []
            native=[w for w in windows if self.native_window(w)]
            if native:
                foreground=getattr(adapter,'foreground_window',lambda:None)()
                current=next((w for w in native if w['id']==foreground),None)
                remembered=next((w for w in native if w['id']==self.state.get('window_id') and w.get('pid')==self.state.get('window_identity',{}).get('pid')),None)
                chosen=current or remembered or (native[0] if len(native)==1 else None)
                if not chosen:
                    return self._save(self._empty('Several TradingView windows are open. Bring the intended TradingView window to the front and check again.','application_ambiguous'))
                focus=getattr(self.desktop,'ensure_focus',None)
                if focus:
                    try: focus(chosen['id'])
                    except Cancelled: raise
                    except Exception as exc: self.trace.append({'name':'Focus installed application','status':'unknown','detail':str(exc)[:400]})
                self.trace.append({'name':'Attach installed application','status':'passed','detail':'Reused an existing TradingView.exe window; no second app was launched.'})
                return self._navigate(chosen['id'],cancel)
            try: result=self.applications.launch('TradingView',cancel)
            except ValueError as exc:
                if 'application not found' not in str(exc).lower() or not hasattr(self.applications,'refresh'): raise
                self.applications.refresh(force=True)
                result=self.applications.launch('TradingView',cancel)
            if not result.get('window_id') or not (result.get('verified') or result.get('observed_running')):
                state=self._empty(result.get('message') or 'The installed TradingView application did not produce a verified visible window.','application_not_found')
                state['diagnostics']['launch']={key:result[key] for key in ('message','errors','ambiguous','matches','focused','observed_running','refresh_recommended') if key in result}
                return self._save(state)
            self.trace.append({'name':'Open installed application','status':'passed' if result.get('verified') else 'unknown','detail':result.get('message') or 'An installed application window was returned; verifying its actual process.'})
            return self._navigate(result['window_id'],cancel)
        except Cancelled: raise
        except Exception as exc:
            state=self._empty('TradingView Desktop connection failed: '+str(exc)[:600],'connection_error')
            state['diagnostics']['actions']=copy.deepcopy(self.trace)
            return self._save(state)
        finally: self.work_lock.release()

    def public(self):
        with self.lock: state=copy.deepcopy(self.state)
        if state.get('verified') and state.get('observed_at'):
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(state['observed_at'])).total_seconds()
            if age>30:
                state.update(connected=False,verified=False,account=None,stage='observation_expired',
                    reason='The TradingView Desktop observation has expired. Check the installed application again; no current account connection is assumed.')
        return state


def public_status(adapter='tradingview_desktop',desktop=None):
    observed=desktop.public() if desktop else TradingViewDesktop._empty('TradingView Desktop has not been inspected in this JARVIS session.')
    return {'adapter':adapter,'label':'TradingView Desktop' if adapter=='tradingview_desktop' else 'JARVIS independent paper simulator',
            'simulated':True,'live_broker_supported':False,'tradingview':observed,
            'data_requirement':'Supply licensed completed NQ candles and synchronized ES candles for SMT. Historical imports are never a live feed.',
            'webhook_note':'JARVIS keeps its authenticated loopback-only API; no public TradingView webhook is enabled.'}
