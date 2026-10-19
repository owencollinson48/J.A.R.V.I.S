"""Request-bound execution, cancellation, audit records and consequence checks."""
import dataclasses
import json
import re
import threading
import time
from pathlib import Path

from .store import identifier, now


class Cancelled(Exception):
    pass


@dataclasses.dataclass
class Decision:
    confirm: bool = False
    reason: str = 'Ordinary action within the current user request'
    risk: str = 'routine'


def safe_log(value, key=''):
    if re.search(r'password|secret|token|credential|api.?key', key, re.I): return '[redacted]'
    if key in {'content', 'text', 'body', 'stdout', 'stderr'} and isinstance(value, str): return f'[{len(value)} characters]'
    if key == 'arguments' and isinstance(value, list): return {'executable': value[0] if value else '', 'argument_count': max(0, len(value) - 1)}
    if isinstance(value, dict): return {k: safe_log(v, k) for k, v in value.items()}
    if isinstance(value, list): return [safe_log(v) for v in value[:20]]
    if isinstance(value, str): return value[:500]
    return value


class RunContext:
    def __init__(self, store, conversation_id, request, workflow_id=None, seconds=300):
        self.store, self.conversation_id, self.request = store, conversation_id, request
        self.id = workflow_id or identifier()
        self.event = threading.Event()
        self.finished = False
        self.deadline = time.monotonic() + seconds
        self.actions = store.row('SELECT count(*) AS n FROM action_log WHERE workflow_id=?',(self.id,))['n'] if workflow_id else 0
        if workflow_id:
            store.execute("UPDATE workflows SET status='running',updated_at=? WHERE id=?", (now(), self.id))
        else:
            store.execute('INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?)',
                          (self.id, conversation_id, request, 'running', '[]', '', now(), now()))

    def check(self):
        if self.finished: raise Cancelled('Task has already ended')
        if self.event.is_set(): raise Cancelled('Task cancelled by the user')
        if time.monotonic() > self.deadline:
            self.event.set()
            raise Cancelled('Task reached its execution time limit')

    def finish(self, status, detail=''):
        self.store.execute('UPDATE workflows SET status=?,detail=?,updated_at=? WHERE id=?', (status, detail[:1000], now(), self.id))
        self.finished = True

    def plan(self, outcome, steps):
        self.check()
        self.store.execute('UPDATE workflows SET plan=?,detail=?,updated_at=? WHERE id=?', (json.dumps(steps), outcome[:1000], now(), self.id))
        return {'workflow_id': self.id, 'outcome': outcome, 'steps': steps}

    def log(self, name, arguments, decision):
        self.check()
        self.actions += 1
        if self.actions > 80: raise Cancelled('Task reached its action limit')
        action_id = identifier()
        self.store.execute('INSERT INTO action_log VALUES(?,?,?,?,?,?,?,?,?,?)',
            (action_id, self.id, self.conversation_id, name, json.dumps(safe_log(arguments)), decision.risk,
             'awaiting_confirmation' if decision.confirm else 'running', '', now(), now()))
        return action_id

    def logged_result(self, action_id, result, status=None):
        success = not result.get('error') and result.get('verified', True) is not False
        self.store.execute('UPDATE action_log SET status=?,result=?,updated_at=? WHERE id=?',
            (status or ('completed' if success else 'failed'), json.dumps(safe_log(result)), now(), action_id))


SENSITIVE = re.compile(r'(^|[\\/])(?:\.ssh|\.aws|\.azure|\.gnupg|credentials?|secrets?|\.env(?:\.[^\\/]*)?|id_rsa|id_ed25519|[^\\/]*\.(?:pem|pfx|p12|key))(?=$|[\\/])', re.I)
SYSTEM_PATH = re.compile(r'[\\/](?:windows[\\/](?:system32|syswow64)|startup|microsoft[\\/]credentials|microsoft[\\/]vault)(?:[\\/]|$)', re.I)
CONSEQUENCE = re.compile(r'\b(?:send|submit|publish|post|buy|purchase|pay|transfer|checkout|place order|delete|uninstall|discard|reset|format drive|administrator|grant access|permissions|security|protection|firewall)\b', re.I)
INTERPRETERS = {'cmd.exe','powershell.exe','pwsh.exe','wt.exe','windowsterminal.exe','python.exe','pythonw.exe','wscript.exe','cscript.exe','mshta.exe','regedit.exe'}


class PermissionPolicy:
    def __init__(self, settings): self.settings = settings

    @staticmethod
    def command_is_routine(arguments):
        if not arguments: return False
        exe = Path(arguments[0].replace('\\', '/')).name.lower().removesuffix('.exe')
        args = arguments[1:]
        if exe in {'hostname','whoami'}: return not args
        if exe == 'ipconfig': return not args or args == ['/all']
        if exe in {'python','python3','py','node'}: return args in [['--version'], ['-V']]
        if exe == 'git':
            # Do not accept git -c aliases, pagers, output paths, external diff drivers, etc.
            return args in [['status'], ['status','--short'], ['log','-5','--oneline'], ['ls-files']]
        return False

    def decide(self, name, args, metadata=None, context=None):
        if not isinstance(context, RunContext):
            raise ValueError('Computer actions require an active user request')
        context.check()
        decision = self._review_decision(name, args, metadata, context)
        if self.settings.values.get('permissions_mode') == 'autonomous':
            return dataclasses.replace(decision, confirm=False,
                reason='The active user request authorizes this action; autonomous mode is enabled')
        return decision

    def _review_decision(self, name, args, metadata, context):
        metadata = metadata or {}
        paths = [str(args[k]) for k in ('path','source','destination','directory') if args.get(k)]
        paths.extend(metadata.get('resolved_paths',[]))
        if metadata.get('change'): paths.extend(str(metadata['change'].get(key,'')) for key in ('written','moved','destination'))
        if any(SENSITIVE.search(path) for path in paths):
            return Decision(True, 'This accesses credential or secret material. Tool results may be sent to your selected model.', 'credentials')
        if name in {'write_document','edit_document','create_folder','move_file'} and any(SYSTEM_PATH.search(path) for path in paths):
            return Decision(True, 'This changes a system, startup, or security-related location', 'system')
        if name == 'run_command' and not self.command_is_routine(args['arguments']):
            return Decision(True, 'This command can execute unrestricted code or change the computer; review the exact arguments', 'command')
        if name == 'open_file' and Path(args['path']).suffix.lower() in {'.exe','.msi','.com','.bat','.cmd','.ps1','.vbs','.reg','.py'}:
            return Decision(True, 'Opening this file can execute code or an installer', 'execution')
        if name in {'launch_application','open_application'}:
            methods = metadata.get('launchers', [])
            targets = [method.get('target','').replace('\\','/').lower() for method in methods]
            if any(method.get('has_arguments') and Path(method.get('executable','').replace('\\','/')).name.lower() in INTERPRETERS for method in methods):
                return Decision(True, 'This shortcut runs an interpreter with arguments', 'command')
            if args.get('arguments') or any(re.search(r'(?:^|/)(?:unins[^/]*|uninstall|setup|install)\.exe$|\.(?:bat|cmd|ps1|vbs)$', target) for target in targets):
                return Decision(True, 'This launch includes executable arguments, a script, or installation/removal software', 'execution')
        if name == 'write_document' and metadata.get('docx_note') and metadata.get('fingerprint'):
            return Decision(True, 'Replacing this existing Word document removes formatting/tables. Use edit_document to preserve them.', 'document_replacement')
        if name in {'desktop_action','browser_action','desktop_pointer'}:
            if name == 'desktop_pointer': return Decision(True, 'Coordinate interaction cannot reliably identify the consequence. Review the target window.', 'unstructured_ui')
            action = args.get('action')
            target = str(metadata.get('target_name', ''))
            if metadata.get('password') or metadata.get('interpreter'):
                return Decision(True, 'This interacts with a password field, terminal, or command interpreter', 'credentials' if metadata.get('password') else 'command')
            if action == 'type_text' and metadata.get('unknown_target'):
                return Decision(True, 'Native text entry targets the focused field using a screenshot rather than an accessible control.', 'unstructured_ui')
            if action in {'click','double_click','right_click','press_keys','fill','set_text','type_text'} and CONSEQUENCE.search(target):
                return Decision(True, 'This control may send information, make a purchase, delete data, or change security settings', 'consequential_ui')
            if re.match(r'\s*(?:javascript|data|file):', args.get('text',''), re.I):
                return Decision(True, 'This text can activate local content or executable browser code', 'execution')
            keys = args.get('keys','').lower().replace(' ', '')
            if action == 'press_keys' and keys == 'enter' and metadata.get('unknown_target'):
                return Decision(True, 'The focused control could not be identified; review before pressing Enter', 'unstructured_ui')
            routine_keys = {'enter','tab','shift+tab','escape','up','down','left','right','home','end','pageup','pagedown','ctrl+s','ctrl+f','ctrl+o','ctrl+l','ctrl+a','ctrl+c','ctrl+v','alt+f4'}
            if action == 'press_keys' and keys not in routine_keys:
                return Decision(True, 'This key sequence has effects beyond ordinary navigation and saving', 'keys')
        if name == 'browser_action' and args.get('action') == 'navigate':
            if CONSEQUENCE.search(args.get('url','').replace('/', ' ')):
                return Decision(True, 'The destination appears to perform a consequential action', 'navigation')
        computer_actions = {'launch_application','open_application','open_file','write_document','edit_document','undo_file_change','create_folder','move_file','run_command','desktop_action','desktop_pointer','browser_action','set_application_alias'}
        if self.settings.values.get('permissions_mode') == 'review' and name in computer_actions:
            return Decision(True, 'Review every computer action is enabled in Settings', 'review')
        if context.actions >= 25 and name in {'write_document','edit_document','move_file'}:
            return Decision(True, 'This workflow is making many file changes; review the next change', 'bulk')
        return Decision()
