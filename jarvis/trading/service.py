"""Shared persistent trading service. The model cannot submit an order directly."""
import asyncio
import copy
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from zoneinfo import ZoneInfo
from decimal import Decimal

from ..store import identifier, now
from .connection import TradingViewDesktop, public_status
from .data import normalize_candles, parse_csv, coverage
from .strategy import DEFAULT_CONFIG, evaluate, validate_config
from .risk import size_position, decimal as risk_decimal
from .simulator import PaperSimulator
from .analytics import analyze
from .backtest import run_backtest
from .research import compare_versions


def stamp(value):
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('Include a timezone in trading dates')
    return parsed.astimezone(timezone.utc)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


class _StopRequested(Exception):
    pass


class TradingService:
    def __init__(self, store, applications, desktop):
        self.store=store
        self._atomic=False
        self.entry_stop=threading.Event()
        self._stop_generation=0
        self.desktop=TradingViewDesktop(applications,desktop)
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS trading_state (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trading_strategies (id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trading_bars (symbol TEXT,timeframe INTEGER,kind TEXT,time TEXT,payload TEXT,source TEXT,PRIMARY KEY(symbol,timeframe,kind,time));
        CREATE TABLE IF NOT EXISTS trading_setups (id TEXT PRIMARY KEY,time TEXT,payload TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS trading_trades (id TEXT PRIMARY KEY,dataset TEXT,run_id TEXT,time TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS trading_runs (id TEXT PRIMARY KEY,created_at TEXT,report TEXT);
        CREATE TABLE IF NOT EXISTS trading_audit (id TEXT PRIMARY KEY,time TEXT,event TEXT,payload TEXT);
        ''')
        store.db.commit()
        with store.lock:
            if not store.row('SELECT id FROM trading_state WHERE id=1'):
                version=identifier()
                store.execute('INSERT INTO trading_strategies VALUES(?,?,?,?)',(version,'V1 · Original NQ strategy',canonical(DEFAULT_CONFIG),now()))
                self._write({'mode':'OFF','last_entry_stop_at':None,'emergency_stop':False,'execution_backend':'tradingview_desktop',
                    'active_strategy_id':version,'initial_equity':'100000','account':{
                        'balance':'100000','equity':'100000','available_funds':'100000','currency':'USD'},
                    'report_timezone':'Australia/Sydney','orders':[],'positions':[],'last_bar':None,'suspension_reason':'','pause_until':None})

    def _execute(self, query, args=()):
        with self.store.lock:
            if self._atomic:
                return self.store.db.execute(query,args).rowcount
            return self.store.execute(query,args)

    @contextmanager
    def transaction(self):
        with self.store.lock:
            if self._atomic:
                yield
                return
            self._atomic=True
            try:
                with self.store.db:
                    yield
            finally:
                self._atomic=False

    def _state(self):
        return json.loads(self.store.row('SELECT payload FROM trading_state WHERE id=1')['payload'])

    def _write(self, state):
        self._execute('INSERT INTO trading_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(canonical(state),))

    def audit(self,event,payload=None):
        self._execute('INSERT INTO trading_audit VALUES(?,?,?,?)',(identifier(),now(),event,canonical(payload or {})))

    def recover(self):
        with self.store.lock:
            state=self._state()
            if state['execution_backend']=='independent_paper':
                state['orders']=PaperSimulator(self.config()).cancel_pending(state['orders'])
                self._write(state)
            state['last_entry_stop_at']=now()
            self._write(state)
            if state['mode']!='OFF':
                state.update(mode='OFF',suspension_reason='Restart recovery paused automated entries. Review data and pending simulator orders before starting again.')
                self._write(state)
                self.audit('restart_suspended')

    def versions(self):
        active=self._state()['active_strategy_id']
        return [{**row,'config':json.loads(row['config']),'active':row['id']==active}
                for row in self.store.rows('SELECT * FROM trading_strategies ORDER BY created_at')]

    def config(self, version=None):
        version=version or self._state()['active_strategy_id']
        row=self.store.row('SELECT * FROM trading_strategies WHERE id=?',(version,))
        if not row: raise ValueError('Unknown strategy version')
        return json.loads(row['config'])

    def propose(self,name,changes):
        if not isinstance(name,str) or not 1<=len(name.strip())<=100: raise ValueError('Provide a strategy name up to 100 characters')
        config=validate_config(changes,base=self.config())
        version={'id':identifier(),'name':name.strip(),'config':config,'active':False,'created_at':now()}
        self._execute('INSERT INTO trading_strategies VALUES(?,?,?,?)',(version['id'],version['name'],canonical(config),version['created_at']))
        self.audit('strategy_proposed',{'id':version['id']})
        return version

    def activate(self,version,approved=False):
        if approved is not True: raise ValueError('Explicit approval is required before replacing the active trading strategy')
        self.config(version)
        with self.store.lock:
            state=self._state()
            if state['mode']!='OFF' or state['positions'] or any(o.get('status') in {'pending','open'} for o in state['orders']):
                raise ValueError('Stop trading and resolve open positions/pending orders before replacing the strategy')
            state['active_strategy_id']=version
            state['last_bar']=None
            self._write(state)
            self.audit('strategy_approved_and_activated',{'id':version})
        return self.snapshot()

    def update_config(self,changes):
        if not isinstance(changes,dict): raise ValueError('Configuration must be an object')
        special=set(changes)&{'execution_backend','initial_equity','report_timezone'}
        if special and set(changes)-special: raise ValueError('Change the execution backend/account separately from strategy proposals')
        if not special: return {'proposed_strategy':self.propose('Proposed configuration',changes),'active_unchanged':True}
        with self.store.lock:
            state=self._state()
            if state['mode']!='OFF' or state['positions'] or any(o.get('status') in {'pending','open'} for o in state['orders']):
                raise ValueError('Backend/account changes require OFF with no open positions or pending orders')
            if 'execution_backend' in changes:
                if changes['execution_backend'] not in {'tradingview_desktop','independent_paper'}: raise ValueError('Choose TradingView Desktop or the independent paper simulator')
                state['execution_backend']=changes['execution_backend']
            if 'report_timezone' in changes:
                zone=changes['report_timezone']
                if not isinstance(zone,str) or len(zone)>100: raise ValueError('Choose a valid reporting timezone')
                try: ZoneInfo(zone)
                except Exception: raise ValueError('Choose a valid reporting timezone') from None
                state['report_timezone']=zone
            if 'initial_equity' in changes:
                if self.store.row("SELECT id FROM trading_trades WHERE dataset='paper' LIMIT 1"): raise ValueError('Cannot rewrite an account that already has closed trade history')
                equity=risk_decimal(changes['initial_equity'],'Initial equity',positive=True)
                if not equity.is_finite() or not Decimal('1')<=equity<=Decimal('1000000000'): raise ValueError('Choose a finite initial simulator equity between 1 and 1 billion')
                state['initial_equity']=str(equity)
                state['account']={k:str(equity) for k in ('balance','equity','available_funds')}
                state['account']['currency']='USD'
            state['last_bar']=None
            self._write(state)
            self.audit('execution_configuration_changed',changes)
        return self.snapshot()

    def bars(self,symbol='NQ',kind='historical',start=None,end=None,limit=20000):
        if symbol not in {'NQ','ES'}: raise ValueError('Only NQ and ES confirmation data are supported')
        args=[symbol,self.config()['timeframe_minutes'],kind]
        query='SELECT payload FROM trading_bars WHERE symbol=? AND timeframe=? AND kind=?'
        if start: query+=' AND time>=?';args.append(stamp(start).isoformat())
        if end: query+=' AND time<=?';args.append(stamp(end).isoformat())
        query+=' ORDER BY time'
        rows=self.store.rows(query+' LIMIT ?',(*args,limit+1))
        if len(rows)>limit: raise ValueError(f'This request exceeds {limit} bars; narrow its historical period. No data was silently removed.')
        return [json.loads(row['payload']) for row in rows]

    def ingest(self,payload):
        allowed={'symbol','source','kind','csv','candles','timeframe_minutes'}
        if not isinstance(payload,dict) or set(payload)-allowed: raise ValueError('Unsupported market data fields')
        symbol=payload.get('symbol','NQ');kind=payload.get('kind','historical')
        if symbol not in {'NQ','ES'} or kind not in {'historical','live'}: raise ValueError('Use NQ or ES data, classified as historical or live')
        source=payload.get('source','')
        if not isinstance(source,str) or not 1<=len(source.strip())<=240: raise ValueError('Identify the legitimate source of your market data')
        timeframe=payload.get('timeframe_minutes',self.config()['timeframe_minutes'])
        if type(timeframe) is not int or not 1<=timeframe<=240: raise ValueError('Use an integer timeframe from 1 to 240 minutes')
        if ('csv' in payload)==('candles' in payload): raise ValueError('Supply either CSV or completed candle rows')
        if 'csv' in payload:
            if not isinstance(payload['csv'],str) or len(payload['csv'])>8000000: raise ValueError('CSV import is limited to 8 MB')
            candles=parse_csv(payload['csv'],symbol=symbol,timeframe_minutes=timeframe)
        else:
            if not isinstance(payload['candles'],list) or len(payload['candles'])>20000: raise ValueError('Supply at most 20000 candle rows per import')
            candles=normalize_candles(payload['candles'],symbol=symbol,timeframe_minutes=timeframe)
        if not candles: raise ValueError('No completed market candles were supplied')
        if len(candles)>20000: raise ValueError('Import at most 20000 candles at a time')
        clock=datetime.now(timezone.utc)
        if any(stamp(bar['time'])>clock+timedelta(seconds=2) for bar in candles): raise ValueError('Future candles are not completed market data')
        with self.store.lock, self.store.db:
            for bar in candles:
                key=(symbol,timeframe,kind,bar['time'])
                existing=self.store.row('SELECT payload FROM trading_bars WHERE symbol=? AND timeframe=? AND kind=? AND time=?',key)
                if existing and existing['payload']!=canonical(bar): raise ValueError('A stored candle differs at this timestamp; existing history was preserved')
            for bar in candles:
                self.store.db.execute('INSERT OR IGNORE INTO trading_bars VALUES(?,?,?,?,?,?)',(symbol,timeframe,kind,bar['time'],canonical(bar),source.strip()))
        self.audit('data_imported',{'symbol':symbol,'kind':kind,'count':len(candles),'source':source.strip()})
        return {'imported':len(candles),'symbol':symbol,'kind':kind,'coverage':coverage(candles,timeframe_minutes=timeframe)}

    def _fresh(self,bar,config,clock=None):
        if not bar: return False
        age=((clock or datetime.now(timezone.utc))-stamp(bar['time'])).total_seconds()
        return -2<=age<=config['max_data_age_seconds']

    def control(self,action,mode=None,close_positions=False,cancel=None):
        if action in {'emergency','pause_today'} or action=='mode' and mode=='OFF':
            self.entry_stop.set()
            self._stop_generation+=1
        if cancel: cancel.check()
        if action=='connect': return self.desktop.connect(cancel)
        with self.store.lock:
            if cancel: cancel.check()
            state=self._state();config=self.config()
            if action in {'emergency','pause_today'} or action=='mode' and mode=='OFF': state['last_entry_stop_at']=now()
            if action=='emergency':
                state.update(mode='OFF',emergency_stop=True,suspension_reason='Persistent emergency stop is active.')
                self._write(state)  # Latch before any optional position work.
                self.audit('emergency_stop')
                state['orders']=PaperSimulator(config).cancel_pending(state['orders'])
                self._write(state)
                if close_positions: return self._close_positions(state,'emergency')
            elif action=='reset_emergency':
                state.update(mode='OFF',emergency_stop=False,suspension_reason='Emergency latch reset; automated entries remain OFF.')
                self._write(state);self.audit('emergency_reset')
            elif action=='pause_today':
                local=datetime.now(ZoneInfo(state.get('report_timezone','Australia/Sydney')))
                state.update(mode='OFF',pause_until=(local+timedelta(days=1)).date().isoformat(),suspension_reason='Trading paused for today in '+state.get('report_timezone','Australia/Sydney')+'.')
                if state['execution_backend']=='independent_paper': state['orders']=PaperSimulator(config).cancel_pending(state['orders'])
                self._write(state);self.audit('paused_today')
            elif action=='cancel_orders':
                if state['execution_backend']!='independent_paper': raise ValueError('TradingView native cancellation has not been verified; no desktop input was sent')
                state['orders']=PaperSimulator(config).cancel_pending(state['orders']);self._write(state);self.audit('pending_orders_cancelled')
            elif action=='close_positions': return self._close_positions(state,'user_requested')
            elif action=='mode':
                if mode not in {'OFF','MONITOR','PAPER','BACKTEST'}: raise ValueError('Choose OFF, MONITOR, PAPER or BACKTEST')
                if mode in {'MONITOR','PAPER'} and state['emergency_stop']: raise ValueError('The persistent emergency stop must be explicitly reset first')
                if mode=='PAPER':
                    if cancel and state.get('last_entry_stop_at'):
                        workflow=self.store.row('SELECT created_at FROM workflows WHERE id=?',(cancel.id,))
                        if workflow and stamp(workflow['created_at'])<=stamp(state['last_entry_stop_at']):
                            raise ValueError('This request predates the latest trading stop. A new user start request is required.')
                    if state.get('pause_until') and datetime.now(ZoneInfo(state.get('report_timezone','Australia/Sydney'))).date().isoformat()<state['pause_until']: raise ValueError('Trading is paused for today')
                    if state['execution_backend']=='tradingview_desktop':
                        status=self.desktop.public()
                        if not status.get('verified') or not status.get('orders_enabled'):
                            raise ValueError(status['reason'])
                    else:
                        latest=self._recent('NQ','live',1)
                        if not latest or not self._fresh(latest[-1],config): raise ValueError('Independent paper trading needs a current licensed NQ feed; historical imports cannot start automated entries')
                if mode=='OFF' and state['execution_backend']=='independent_paper':
                    state['orders']=PaperSimulator(config).cancel_pending(state['orders'])
                if mode in {'MONITOR','PAPER'}: self.entry_stop.clear()
                state.update(mode=mode,suspension_reason='')
                self._write(state);self.audit('mode_changed',{'mode':mode})
            else: raise ValueError('Unknown trading control')
        return self.snapshot()

    def _recent(self,symbol,kind,limit=1000):
        rows=self.store.rows('SELECT payload FROM trading_bars WHERE symbol=? AND timeframe=? AND kind=? ORDER BY time DESC LIMIT ?',
                             (symbol,self.config()['timeframe_minutes'],kind,limit))
        return [json.loads(row['payload']) for row in reversed(rows)]

    def _save_trades(self,trades,dataset='paper',run_id=None):
        for trade in trades:
            trade=copy.deepcopy(trade);trade['dataset']=dataset
            trade['execution_backend']='independent_paper' if dataset=='paper' else 'historical_backtest'
            trade_id=str(trade.get('trade_id') or trade.get('id') or identifier())
            trade['trade_id']=dataset+':'+str(run_id or 'account')+':'+trade_id
            trade_id=trade['trade_id']
            self._execute('INSERT OR IGNORE INTO trading_trades VALUES(?,?,?,?,?)',
                (trade_id,dataset,run_id,trade.get('exit_time') or trade.get('exit_timestamp') or now(),canonical(trade)))

    def _portfolio(self,state):
        # Loss budgets include realised and current unrealised losses and risk
        # already reserved by pending orders. Profit does not increase defaults.
        zone=ZoneInfo(state.get('report_timezone','Australia/Sydney'))
        clock=datetime.now(zone);day=clock.date();week=day-timedelta(days=day.weekday())
        daily=Decimal('0');weekly=Decimal('0')
        for trade in self.journal('paper')['trades']:
            time=trade.get('exit_time') or trade.get('exit_timestamp')
            if not time: continue
            net=Decimal(str(trade.get('net_pnl',0)))
            if stamp(time).astimezone(zone).date()>=week: weekly+=net
            if stamp(time).astimezone(zone).date()==day: daily+=net
        unrealized=sum((min(Decimal(str(p.get('unrealized_pnl','0'))),Decimal('0')) for p in state['positions']),Decimal('0'))
        pending=[{**o,'entry':o.get('entry',o.get('entry_price')),'unrealized_pnl':'0'} for o in state['orders'] if o.get('status') in {'pending','open'}]
        return {'positions':[*state['positions'],*pending],'daily_loss':str(max(Decimal('0'),-daily-unrealized)),
                'weekly_loss':str(max(Decimal('0'),-weekly-unrealized))}

    def _close_positions(self,state,reason):
        if state['execution_backend']!='independent_paper': raise ValueError('TradingView Desktop position closure has not been verified; no desktop input was sent')
        config=self.config();bars=self._recent('NQ','live',1)
        if state['positions'] and (not bars or not self._fresh(bars[-1],config)): raise ValueError('Position closure needs a fresh NQ price; positions were retained')
        prices={'NQ':bars[-1]} if bars else {}
        result=PaperSimulator(config).close_positions(state['account'],state['positions'],prices,reason=reason)
        state.update(account=result['account'],positions=result['positions'])
        self._save_trades(result['closed_trades']);self._write(state);self.audit('positions_closed',{'reason':reason})
        return self.snapshot()

    def tick(self):
        try: return self._tick()
        except _StopRequested: return

    def _tick(self):
        with self.transaction():
            generation=self._stop_generation
            state=self._state();config=self.config()
            if state['mode'] not in {'MONITOR','PAPER','OFF'}: return
            if state['mode'] in {'MONITOR','PAPER'} and self.entry_stop.is_set(): return
            bars=self._recent('NQ','live')
            if not bars or not self._fresh(bars[-1],config):
                if state['mode'] in {'MONITOR','PAPER'} or state['positions']:
                    state['suspension_reason']='Market data is missing or stale. No new orders can be submitted.'
                    if state['mode']=='PAPER': state['mode']='OFF'
                    self._write(state)
                return
            if state['execution_backend']=='tradingview_desktop':
                if state['mode']=='PAPER':
                    state.update(mode='OFF',suspension_reason='TradingView Desktop order execution is not verified. New entries suspended.')
                    self._write(state)
                if state['mode']!='MONITOR': return
            if state['mode']=='OFF' and state['execution_backend']=='independent_paper':
                state['orders']=PaperSimulator(config).cancel_pending(state['orders'])
            previous=state['last_bar']
            candidates=[(index,bar) for index,bar in enumerate(bars)
                        if previous is not None and stamp(bar['time'])>stamp(previous)]
            if previous is None: candidates=[(len(bars)-1,bars[-1])]
            for index,bar in candidates:
                pending=any(o.get('status')=='pending' for o in state['orders'])
                if state['execution_backend']=='independent_paper' and (state['positions'] or pending):
                    if state['last_bar'] and (stamp(bar['time'])-stamp(state['last_bar'])).total_seconds()>config['timeframe_minutes']*90:
                        state.update(mode='OFF',suspension_reason='Missing live bars prevent reliable position/order reconciliation. Import the missing live data and inspect positions; no fills were invented.')
                        self._write(state);return
                    account={**state['account'],**{k:v for k,v in self._portfolio(state).items() if k!='positions'}}
                    result=PaperSimulator(config).on_bar(account,state['orders'],state['positions'],bar)
                    state.update(account=result['account'],orders=result['orders'],positions=result['positions'])
                    self._save_trades(result['closed_trades'])
                state['last_bar']=bar['time']
                # Batch arrival must not create orders at earlier prices when a
                # later candle is already known. Existing positions replay all
                # actual stored bars; new entries use only the freshest close.
                if index!=len(bars)-1 or state['mode'] not in {'MONITOR','PAPER'} or state['emergency_stop']: continue
                correlated=self._recent('ES','live')
                setup=evaluate(bars[:index+1],config,correlated=correlated)
                setup['strategy_version']=state['active_strategy_id']
                setup['setup_id']=state['active_strategy_id']+':'+setup['setup_id']
                previous_setup=self.store.row('SELECT status FROM trading_setups WHERE id=?',(setup['setup_id'],))
                if not previous_setup or previous_setup['status']!='submitted_simulated':
                    status='qualified' if setup['passed'] else 'rejected'
                    if setup['passed'] and state['mode']=='PAPER':
                        sizing=size_position(state['account']['equity'],setup['entry'],setup['stop_loss'],config,self._portfolio(state))
                        setup['risk']=sizing
                        if sizing['passed']:
                            if self.entry_stop.is_set() or self._stop_generation!=generation: raise _StopRequested()
                            order=PaperSimulator(config).submit(state['account'],setup,sizing['quantity'],
                                order_type=setup.get('order_type','market'),limit_price=setup['entry'] if setup.get('order_type')=='limit' else None)
                            state['orders'].append(order);status='submitted_simulated'
                            self.audit('simulator_order_submitted',{'setup_id':setup['setup_id'],'order_id':order.get('id')})
                        else:
                            status='risk_rejected'
                            if any('loss limit' in reason.lower() for reason in sizing['reasons']):
                                state.update(mode='OFF',suspension_reason=' '.join(sizing['reasons']))
                    self._execute('INSERT INTO trading_setups VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET time=excluded.time,payload=excluded.payload,status=excluded.status',(setup['setup_id'],setup['time'],canonical(setup),status))
            if self._stop_generation!=generation: raise _StopRequested()
            self._write(state)

    async def loop(self):
        while True:
            try: await asyncio.to_thread(self.tick)
            except asyncio.CancelledError: raise
            except Exception as exc:
                with self.store.lock:
                    state=self._state();state.update(mode='OFF',suspension_reason='Trading monitoring stopped: '+str(exc)[:500]);self._write(state)
                    self.audit('monitor_failure',{'error':str(exc)[:500]})
            await asyncio.sleep(2)

    def backtest(self,payload):
        if set(payload)-{'start','end','symbol','strategy_id','initial_equity'}: raise ValueError('Unsupported backtest fields')
        if payload.get('symbol','NQ')!='NQ': raise ValueError('ES is confirmation data only; only NQ is traded')
        start,end=payload.get('start'),payload.get('end')
        if start and end and stamp(start)>=stamp(end): raise ValueError('Backtest start must precede its end')
        config=self.config(payload.get('strategy_id'))
        candles=self._history_for_config('NQ',config,start,end)
        if not candles: raise ValueError('No historical NQ data is available for this strategy timeframe and period. Import legitimate data first.')
        correlated=self._history_for_config('ES',config,start,end)
        report=run_backtest(candles,config,correlated=correlated,initial_equity=payload.get('initial_equity',self._state()['initial_equity']),start=start,end=end,strategy_version=payload.get('strategy_id') or self._state()['active_strategy_id'])
        report.update(initial_equity=str(payload.get('initial_equity',self._state()['initial_equity'])) ,id=identifier(),dataset='backtest',strategy_id=payload.get('strategy_id') or self._state()['active_strategy_id'],created_at=now())
        for trade in report['trades']: trade['strategy_version']=report['strategy_id']
        with self.transaction():
            self._execute('INSERT INTO trading_runs VALUES(?,?,?)',(report['id'],report['created_at'],canonical(report)))
            self._save_trades(report['trades'],'backtest',report['id'])
            self.audit('backtest_completed',{'id':report['id'],'trades':len(report['trades'])})
        return report

    def _history_for_config(self,symbol,config,start,end):
        # Strategy versions with different timeframes require matching imported
        # candles. Never relabel five-minute candles as one-minute observations.
        args=[symbol,config['timeframe_minutes'],'historical']
        query='SELECT payload FROM trading_bars WHERE symbol=? AND timeframe=? AND kind=?'
        if start:
            from .strategy import required_history
            warm=self.store.rows(query+' AND time<? ORDER BY time DESC LIMIT ?',(*args,stamp(start).isoformat(),required_history(config)))
            warm=[json.loads(row['payload']) for row in reversed(warm)]
            query+=' AND time>=?';args.append(stamp(start).isoformat())
        else: warm=[]
        if end: query+=' AND time<=?';args.append(stamp(end).isoformat())
        rows=self.store.rows(query+' ORDER BY time LIMIT 20001',args)
        if len(rows)>20000: raise ValueError('Backtest is limited to 20000 candles per request; narrow the period')
        return warm+[json.loads(row['payload']) for row in rows]

    def run_report(self,run_id):
        row=self.store.row('SELECT report FROM trading_runs WHERE id=?',(run_id,))
        if not row: raise ValueError('Unknown backtest report')
        return json.loads(row['report'])

    def research(self,payload):
        if set(payload)-{'strategy_ids','start','end'}: raise ValueError('Unsupported research fields')
        ids=payload.get('strategy_ids') or [v['id'] for v in self.versions()]
        if not isinstance(ids,list) or not 1<=len(ids)<=6: raise ValueError('Compare one to six strategy versions')
        known={v['id']:v for v in self.versions()}
        if any(not isinstance(v,str) or v not in known for v in ids): raise ValueError('Unknown strategy version')
        versions=[{'id':v,'name':known[v]['name'],'config':self.config(v)} for v in ids]
        configs=[v['config'] for v in versions]
        if any(c['timeframe_minutes']!=configs[0]['timeframe_minutes'] for c in configs): raise ValueError('Controlled comparisons require identical candle timeframes')
        candles=self._history_for_config('NQ',configs[0],payload.get('start'),payload.get('end'))
        if not candles: raise ValueError('Import historical NQ candles before strategy research')
        if len(candles)>3000: raise ValueError('Research experiments are limited to 3000 candles per request; narrow the dates')
        correlated=self._history_for_config('ES',configs[0],payload.get('start'),payload.get('end'))
        return compare_versions(candles,versions,correlated=correlated,initial_equity=self._state()['initial_equity'])

    def journal(self,dataset='paper',query='',start=None,end=None,run_id=None):
        if dataset not in {'paper','backtest'}: raise ValueError('Choose paper or backtest records')
        args=[dataset];sql='SELECT payload FROM trading_trades WHERE dataset=?'
        if run_id: sql+=' AND run_id=?';args.append(run_id)
        trades=[json.loads(row['payload']) for row in self.store.rows(sql+' ORDER BY time',args)]
        if start: trades=[t for t in trades if stamp(t.get('exit_time') or t.get('exit_timestamp'))>=stamp(start)]
        if end: trades=[t for t in trades if stamp(t.get('exit_time') or t.get('exit_timestamp'))<=stamp(end)]
        if query: trades=[t for t in trades if str(query).casefold() in canonical(t).casefold()]
        # Backtest runs are independent experiments; do not pretend their
        # combined returns form one compounded account.
        initial=self.run_report(run_id).get('initial_equity',self._state()['initial_equity']) if dataset=='backtest' and run_id else self._state()['initial_equity']
        report=analyze(trades,initial_equity=initial)
        if dataset=='backtest' and not run_id:
            report.setdefault('assumptions',[]).append('Backtest runs are independent experiments. Select one run for account-level equity, drawdown and risk-adjusted return metrics.')
            report['equity_curve']=[]
            for key in ('equity_curve','drawdown'): report.get('charts',{})[key]=[]
            for key in ('maximum_drawdown','max_drawdown','average_drawdown','recovery_factor','sharpe_ratio','sortino_ratio','equity_growth','equity_growth_pct','ending_equity','initial_equity'):
                if key in report['metrics']: report['metrics'][key]=None
        return {'dataset':dataset,'execution_backend':'independent_paper' if dataset=='paper' else 'historical_backtest','tradingview_records':False,'trades':trades,**report}

    def snapshot(self):
        with self.store.lock:
            state=self._state();config=self.config();summaries=[]
            for symbol in ('NQ','ES'):
                for kind in ('historical','live'):
                    rows=self.store.rows('SELECT count(*) AS count,min(time) AS start,max(time) AS end FROM trading_bars WHERE symbol=? AND timeframe=? AND kind=?',
                                        (symbol,config['timeframe_minutes'],kind))[0]
                    if rows['count']:
                        source=self.store.row('SELECT source FROM trading_bars WHERE symbol=? AND timeframe=? AND kind=? ORDER BY time DESC LIMIT 1',(symbol,config['timeframe_minutes'],kind))['source']
                        summaries.append({**rows,'symbol':symbol,'kind':kind,'source':source,
                            'stale':kind=='live' and not self._fresh({'time':rows['end']},config)})
            analysis=self.journal('paper')
            connection=public_status(state['execution_backend'],self.desktop)
            account=state['account'] if state['execution_backend']=='independent_paper' else connection['tradingview'].get('account')
            return {**state,'account':account,'simulator_account':state['account'],'connection':connection,
                'config':{**config,'execution_backend':state['execution_backend'],'initial_equity':state['initial_equity'],'report_timezone':state.get('report_timezone','Australia/Sydney')},
                'strategy_versions':self.versions(),'data':summaries,
                'candles':self._recent('NQ','live',120) or self._recent('NQ','historical',120),
                'setups':[{**json.loads(row['payload']),'status':row['status']} for row in self.store.rows('SELECT * FROM trading_setups ORDER BY time DESC LIMIT 30')],
                'paper_history_label':'Independent paper simulator history (not TradingView)','metrics':analysis['metrics'],'charts':analysis.get('charts',{}),'analytics':{k:analysis[k] for k in ('metrics','charts','equity_curve','assumptions') if k in analysis},
                'recent_backtests':[{'id':r['id'],'created_at':r['created_at']} for r in self.store.rows('SELECT id,created_at FROM trading_runs ORDER BY created_at DESC LIMIT 20')]}
