"""Native TradingView connection regression tests; no actual app or orders.

Adapters expose only the observed window/controls for each stage. They record
all navigation input so a connection test also proves no buy/sell input ran.
"""
import copy
from types import SimpleNamespace
from decimal import Decimal

import pytest

from jarvis.trading.connection import TradingViewDesktop


WINDOW = {'id':7,'pid':44,'process':'TradingView.exe','title':'NQ — TradingView'}
PAPER = {'id':'paper-account','name':'Account','type':'ComboBox','text':'Paper Trading'}
BALANCES = [
    {'id':'balance','name':'Balance USD 100,000.00','type':'Text'},
    {'id':'equity','name':'Equity USD 100,125.00','type':'Text'},
    {'id':'funds','name':'Available funds USD 98,000.00','type':'Text'},
]


def observed(*elements, **extra):
    return {'window':copy.deepcopy(WINDOW),'elements':copy.deepcopy(list(elements)),**extra}


class Desktop:
    def __init__(self, observations):
        self.observations=list(observations)
        self.index=0
        self.inputs=[]
        self.captures=[]
        self.window_error=None
        self.descendant_error=None
        self.inspect_error=None
        self.act_error=None
        self.capture_error=None
        self.controls=[]
        self.scope_chains={}
        self.post_error=None

    def inspect(self, window_id, cancel=None):
        if cancel: cancel.check()
        if self.inspect_error: raise self.inspect_error
        return copy.deepcopy(self.observations[min(self.index,len(self.observations)-1)])

    def native_window(self, window_id):
        return copy.deepcopy(WINDOW)

    def window(self, window_id):
        if self.window_error: raise self.window_error
        def descendants(depth):
            if self.descendant_error: raise self.descendant_error
            return self.controls
        return copy.deepcopy(WINDOW),SimpleNamespace(descendants=descendants)

    def screenshot(self, window_id, cancel=None):
        self.captures.append(window_id)
        if self.capture_error: raise self.capture_error
        return {'image_id':'a'*32,'window_id':window_id,'width':100,'height':80}

    def preflight(self, args):
        item=next(element for element in self.inspect(args['window_id'])['elements'] if element.get('id')==args.get('element_id'))
        return {'target_name':item['name'],'password':False,'signature':'observed-control'}

    def act(self, args, preview, cancel=None):
        self.inputs.append(copy.deepcopy(args))
        if self.act_error: raise self.act_error
        self.index=min(self.index+1,len(self.observations)-1)
        result={'performed':args['action'],'window_id':args['window_id'],'observation':self.inspect(args['window_id'])}
        if self.post_error: result['observation_error']=self.post_error
        return result

    def target(self,window_id,element_id):
        control=self.scope_chains[element_id]
        return copy.deepcopy(WINDOW),SimpleNamespace(),control,{'name':'Connect','type':'Button'}


@pytest.fixture
def make_adapter(monkeypatch):
    monkeypatch.setattr('jarvis.trading.connection.platform.system',lambda:'Windows')
    def make(*observations):
        desktop=Desktop(observations or [observed()])
        launches=[]
        def launch(*args):
            launches.append(args)
            return {'verified':True,'window_id':7}
        applications=SimpleNamespace(launch=launch)
        adapter=TradingViewDesktop(applications,desktop)
        return SimpleNamespace(adapter=adapter,desktop=desktop,applications=applications,launches=launches)
    return make


@pytest.mark.parametrize('failure',['window','descendants'])
def test_wrapper_failure_keeps_already_observed_account_selector_and_capture(make_adapter,failure):
    env=make_adapter(observed(PAPER,*BALANCES))
    if failure=='window': env.desktop.window_error=OSError('UIA wrapper unavailable')
    else: env.desktop.descendant_error=OSError('UIA descendants unavailable')
    result=env.adapter.observe(7)
    assert result['selected_account']=='Paper Trading'
    assert Decimal(result['account']['equity'])==Decimal('100125.00')
    assert result['verified'] and result['orders_enabled'] is False
    assert env.desktop.captures==[7]
    assert result['screenshot']['image_id']=='a'*32
    assert not env.desktop.inputs


def test_inspection_failure_still_captures_known_native_window_for_diagnosis(make_adapter):
    env=make_adapter(observed())
    env.desktop.inspect_error=OSError('UI Automation unavailable')
    result=env.adapter.observe(7)
    assert not result['verified'] and not result['orders_enabled']
    assert env.desktop.captures==[7]
    assert result['screenshot']['image_id']=='a'*32
    assert 'UI Automation unavailable' in str(result)
    assert not env.desktop.inputs


def test_failed_capture_does_not_erase_observed_account_and_inspection_failure(make_adapter):
    env=make_adapter(observed(PAPER,*BALANCES))
    env.desktop.capture_error=OSError('Protected pixels unavailable')
    result=env.adapter.observe(7)
    assert result['selected_account']=='Paper Trading' and Decimal(result['account']['equity'])==Decimal('100125.00')
    assert not result['orders_enabled']
    assert 'Protected pixels unavailable' in str(result)
    assert not env.desktop.inputs


def test_partial_balances_preserve_unknown_fields_without_fabrication(make_adapter):
    env=make_adapter(observed(PAPER,BALANCES[0],BALANCES[1]))
    result=env.adapter.observe(7)
    assert result['selected_account']=='Paper Trading'
    assert Decimal(result['account']['balance'])==Decimal('100000.00')
    assert Decimal(result['account']['equity'])==Decimal('100125.00')
    assert result['account'].get('available_funds') is None
    assert not result['orders_enabled']


@pytest.mark.parametrize('role',['MenuItem','ListItem','Text','Button'])
def test_plain_paper_trading_option_is_not_selected_account_proof(make_adapter,role):
    env=make_adapter(observed({'id':'paper','name':'Paper Trading','type':role},*BALANCES))
    result=env.adapter.observe(7)
    assert not result['verified'] and not result['connected'] and not result['orders_enabled']
    assert result['account'] is None
    assert not env.desktop.inputs


def test_non_native_browser_title_does_not_supply_account_evidence_or_navigation(make_adapter):
    screen=observed(PAPER,*BALANCES)
    screen['window']['process']='msedge.exe'
    env=make_adapter(screen)
    result=env.adapter.observe(7)
    assert not result['verified'] and not result['connected']
    assert result['account'] is None and not env.desktop.inputs


def test_connect_preserves_specific_launch_failure_message(make_adapter):
    env=make_adapter(observed())
    env.applications.launch=lambda *args:{'verified':False,'message':'Two indexed TradingView editions are ambiguous','matches':[{'id':'one'},{'id':'two'}]}
    result=env.adapter.connect()
    assert not result['verified']
    assert 'ambiguous' in str(result).lower()
    assert not env.desktop.inputs


def test_selected_account_picker_menu_item_is_not_connected_paper_account(make_adapter):
    env=make_adapter(observed({'id':'paper','name':'Paper Trading','type':'MenuItem'},*BALANCES))
    env.desktop.controls=[SimpleNamespace(
        is_visible=lambda:True,
        element_info=SimpleNamespace(name='Paper Trading',control_type='MenuItem'),
        iface_selection_item=SimpleNamespace(CurrentIsSelected=True),
    )]
    result=env.adapter.observe(7)
    assert not result['verified'] and result['account'] is None
    assert not result['orders_enabled'] and not env.desktop.inputs



def test_already_open_native_window_attaches_without_relaunch_or_stale_index(make_adapter):
    env=make_adapter(observed(PAPER,*BALANCES))
    env.applications.adapter=SimpleNamespace(
        supported=True,windows=lambda:[{'id':8,'pid':55,'process':'chrome.exe','title':'TradingView'},copy.deepcopy(WINDOW)],
        foreground_window=lambda:7,
    )
    env.applications.launch=lambda *args:pytest.fail('An already open native app must not need indexed launch')
    result=env.adapter.connect()
    assert result['verified'] and result['window_id']==7
    assert not env.desktop.inputs


def test_bounded_connection_navigates_only_fresh_observed_paper_controls(make_adapter):
    env=make_adapter(
        observed({'id':'panel-1','name':'Trading Panel','type':'TabItem'}),
        observed({'id':'paper-2','name':'Paper Trading','type':'Button'}),
        observed({'id':'dialog','name':'Paper Trading','type':'Window'},
                 {'id':'connect-3','name':'Connect','type':'Button'},
                 {'id':'buy','name':'Buy','type':'Button'}),
        observed(PAPER,*BALANCES,{'id':'sell','name':'Sell','type':'Button'}),
    )
    env.adapter.connect_scope=lambda window_id,element_id:element_id=='connect-3'
    result=env.adapter.connect()
    assert result['verified'] and not result['orders_enabled']
    assert env.desktop.inputs==[
        {'action':'click','window_id':7,'element_id':'panel-1'},
        {'action':'click','window_id':7,'element_id':'paper-2'},
        {'action':'click','window_id':7,'element_id':'connect-3'},
    ]
    assert env.desktop.captures and len(env.desktop.inputs)<=4


def test_unscoped_connect_never_guesses_from_paper_text_or_clicks_order_controls(make_adapter):
    env=make_adapter(observed(
        {'id':'paper-label','name':'Paper Trading','type':'Text'},
        {'id':'connect-live','name':'Connect','type':'Button'},
        {'id':'buy','name':'Buy Market','type':'Button'},
        {'id':'sell','name':'Sell Market','type':'Button'},
    ))
    env.adapter.connect_scope=lambda *args:False
    result=env.adapter.connect()
    assert not result['verified'] and not result['orders_enabled']
    assert not env.desktop.inputs


@pytest.mark.parametrize('failure',['input_exception','observation_error'])
def test_uncertain_delivered_navigation_is_not_replayed(make_adapter,failure):
    env=make_adapter(observed({'id':'panel','name':'Trading Panel','type':'TabItem'}))
    if failure=='input_exception':env.desktop.act_error=OSError('Input may have delivered; observation failed')
    else:env.desktop.post_error='Input delivered but accessibility observation failed'
    result=env.adapter.connect()
    assert not result['orders_enabled'] and not result['verified']
    assert env.desktop.inputs==[{'action':'click','window_id':7,'element_id':'panel'}]
    assert env.desktop.captures


def parent_control(name,kind,parent=None):
    return SimpleNamespace(element_info=SimpleNamespace(name=name,control_type=kind),parent=lambda:parent)


@pytest.mark.parametrize('name,kind,expected',[
    ('Paper Trading','Dialog',True),
    ('Paper Trading by TradingView','Pane',True),
    ('Paper Trading account','Group',False),
    ('Live broker','Dialog',False),
    ('TradingView — Paper Trading','Window',False),
    ('Paper Trading','Text',False),
])
def test_connect_button_requires_exact_native_paper_container_ancestry(make_adapter,name,kind,expected):
    env=make_adapter(observed())
    ancestor=parent_control(name,kind)
    child=parent_control('Connect','Button',ancestor)
    env.desktop.scope_chains['button']=child
    assert env.adapter.connect_scope(7,'button') is expected
    assert not env.desktop.inputs


def test_recently_installed_app_refreshes_index_once_after_not_found(make_adapter):
    env=make_adapter(observed(PAPER,*BALANCES))
    launches=[];refreshes=[]
    def launch(*args):
        launches.append(args)
        if len(launches)==1:raise ValueError('Application not found. Refresh the app index or add its install folder in Settings.')
        return {'verified':True,'window_id':7}
    env.applications.launch=launch
    env.applications.refresh=lambda force=False:refreshes.append(force)
    result=env.adapter.connect()
    assert result['verified'] and len(launches)==2 and refreshes==[True]
    assert not env.desktop.inputs


def test_activation_error_does_not_refresh_or_replay_the_launch(make_adapter):
    env=make_adapter(observed())
    launches=[]
    def launch(*args):
        launches.append(args)
        raise OSError('Activation may have delivered but window observation failed')
    env.applications.launch=launch
    env.applications.refresh=lambda **kwargs:pytest.fail('An uncertain delivered launch must not be retried')
    result=env.adapter.connect()
    assert not result['verified'] and len(launches)==1 and not env.desktop.inputs
    assert 'Activation may have delivered' in str(result)


def test_visible_native_window_can_be_inspected_when_foreground_focus_is_unverified(make_adapter):
    env=make_adapter(observed(PAPER,*BALANCES))
    env.applications.launch=lambda *args:{'verified':False,'observed_running':True,'focused':False,'window_id':7,
        'message':'The application is visible but foreground focus was denied'}
    result=env.adapter.connect()
    assert result['verified'] and result['window_id']==7
    assert not result['orders_enabled'] and not env.desktop.inputs



@pytest.mark.parametrize('container,expected',[
    ('Paper Trading',True),('Paper Trading by TradingView',True),
    ('Live broker',False),('TradingView — Paper Trading',False),
])
def test_observed_disconnect_proves_paper_connection_only_inside_exact_paper_container(make_adapter,container,expected):
    env=make_adapter(observed({'id':'disconnect','name':'Disconnect','type':'Button'},BALANCES[0]))
    ancestor=parent_control(container,'Dialog')
    disconnect=parent_control('Disconnect','Button',ancestor)
    disconnect.is_visible=lambda:True
    env.desktop.controls=[disconnect]
    result=env.adapter.observe(7)
    assert result['verified'] is expected
    assert result['connected'] is expected
    if expected:
        assert result['selected_account']=='Paper Trading'
        assert Decimal(result['account']['balance'])==Decimal('100000.00')
        assert result['account'].get('equity') is None
    else: assert result['account'] is None
    assert not result['orders_enabled'] and not env.desktop.inputs


def test_connection_report_is_authenticated_read_only_and_cannot_dispatch_native_input(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from jarvis.app import create_app
    app=create_app(tmp_path/'private')
    path='/api/trading/connection/report'
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.get(path).status_code==401
        client.get('/')
        trading=app.state.tools.trading
        def forbidden(*args,**kwargs):pytest.fail('A report download must not inspect, connect, capture or place orders')
        monkeypatch.setattr(trading,'control',forbidden)
        monkeypatch.setattr(trading.desktop,'connect',forbidden)
        monkeypatch.setattr(app.state.tools.desktop,'inspect',forbidden)
        monkeypatch.setattr(app.state.tools.desktop,'screenshot',forbidden)
        monkeypatch.setattr(app.state.tools.desktop,'act',forbidden)
        before=app.state.store.rows('SELECT * FROM trading_state')
        audit=app.state.store.rows('SELECT * FROM trading_audit')
        result=client.get(path)
        assert result.status_code==200
        report=result.json()
        assert report['schema_version']==1
        assert report['orders_enabled'] is False
        assert report['native_order_implementation']=='unavailable'
        assert report['connection']['verified'] is False
        assert 'image_bytes' not in report
        assert app.state.store.rows('SELECT * FROM trading_state')==before
        assert app.state.store.rows('SELECT * FROM trading_audit')==audit
        assert client.get(path,headers={'Origin':'https://foreign.example'}).status_code==403
        assert client.get('http://foreign.example'+path).status_code==403
