import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid.uuid4().hex


def due_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Include a time zone in the due date")
        return parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Use an ISO 8601 date with a time zone") from exc


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, conversation_id TEXT NOT NULL,
          role TEXT NOT NULL, content TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id, seq);
        CREATE TABLE IF NOT EXISTS goals (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, details TEXT DEFAULT '', status TEXT DEFAULT 'active',
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, details TEXT DEFAULT '', due_at TEXT,
          status TEXT DEFAULT 'open', goal_id TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS reminders (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, due_at TEXT NOT NULL, fired_at TEXT,
          acknowledged INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
          name TEXT NOT NULL, arguments TEXT NOT NULL, preview TEXT NOT NULL,
          status TEXT DEFAULT 'pending', result TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS workflows (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL,
          plan TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS action_log (
          id TEXT PRIMARY KEY, workflow_id TEXT, conversation_id TEXT NOT NULL, name TEXT NOT NULL,
          arguments TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
        """)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(approvals)')}
        if 'workflow_id' not in columns:
            self.db.execute('ALTER TABLE approvals ADD COLUMN workflow_id TEXT')
        if 'action_id' not in columns:
            self.db.execute('ALTER TABLE approvals ADD COLUMN action_id TEXT')
        self.db.commit()

    def rows(self, query, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(query, args).fetchall()]

    def row(self, query, args=()):
        rows = self.rows(query, args)
        return rows[0] if rows else None

    def execute(self, query, args=()):
        with self.lock, self.db:
            return self.db.execute(query, args).rowcount

    def conversation(self, title="New conversation"):
        item = {"id": identifier(), "title": title[:100], "created_at": now(), "updated_at": now()}
        self.execute("INSERT INTO conversations VALUES (:id,:title,:created_at,:updated_at)", item)
        return item

    def message(self, conversation_id, payload):
        item = {"id": identifier(), "conversation_id": conversation_id,
                "role": payload["role"], "content": payload.get("content") or "",
                "payload": json.dumps(payload), "created_at": now()}
        self.execute("""INSERT INTO messages(id,conversation_id,role,content,payload,created_at)
                        VALUES(:id,:conversation_id,:role,:content,:payload,:created_at)""", item)
        self.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conversation_id))
        return item

    def history(self, conversation_id):
        return self.rows("SELECT id,role,content,created_at FROM messages WHERE conversation_id=? ORDER BY seq", (conversation_id,))

    def model_history(self, conversation_id):
        # Keep whole turns so an assistant tool call never loses its tool result.
        boundary = self.row("""SELECT seq FROM messages WHERE conversation_id=? AND role='user'
                               ORDER BY seq DESC LIMIT 1 OFFSET 11""", (conversation_id,))
        items = self.rows("SELECT payload FROM messages WHERE conversation_id=? AND seq>=? ORDER BY seq",
                          (conversation_id, boundary["seq"] if boundary else 0))
        return [json.loads(item["payload"]) for item in items]

    def search_memory(self, query, conversation_id=None, limit=8):
        words = [w for w in re.findall(r"[\w'-]+", query.lower()) if len(w) > 2]
        stop = {"the", "and", "that", "with", "what", "have", "can", "you", "for", "about", "please", "this"}
        words = list(dict.fromkeys(w for w in words if w not in stop))[:12]
        score = " + ".join("CASE WHEN instr(lower(content), ?) > 0 THEN 1 ELSE 0 END" for _ in words) or "0"
        # Rank across the complete archive, not just the last few conversations.
        memories = self.rows(f"SELECT *, ({score}) AS relevance FROM memories ORDER BY relevance DESC, created_at DESC LIMIT 60", tuple(words))
        hits = self.rows(f"""SELECT role,content,conversation_id,created_at,({score}) AS relevance FROM messages
                    WHERE role IN ('user','assistant') AND conversation_id != ? AND content != ''
                    ORDER BY relevance DESC, seq DESC LIMIT ?""", (*words, conversation_id or "", limit)) if words else []
        return {"memories": memories, "past_conversations": [{**i, "content": i["content"][:2000]} for i in hits if i['relevance'] > 0]}

    def create_item(self, kind, values):
        definitions = {
            "tasks": {"title", "details", "due_at", "goal_id"},
            "goals": {"title", "details"},
            "reminders": {"title", "due_at"},
            "memories": {"content"},
        }
        allowed = definitions[kind]
        values = {k: v for k, v in values.items() if k in allowed}
        required = "content" if kind == "memories" else "title"
        if not isinstance(values.get(required), str) or not values[required].strip():
            raise ValueError(f"{required.title()} is required")
        if len(values[required]) > 4000:
            raise ValueError("Keep this entry under 4,000 characters")
        values[required] = values[required].strip()
        if "due_at" in values:
            values["due_at"] = due_date(values["due_at"])
        if kind == "reminders" and not values.get("due_at"):
            raise ValueError("A reminder needs a date and time")
        values.update(id=identifier(), created_at=now())
        columns = ",".join(values)
        self.execute(f"INSERT INTO {kind} ({columns}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
        return self.row(f"SELECT * FROM {kind} WHERE id=?", (values["id"],))

    def update_item(self, kind, item_id, values):
        allowed = {"tasks": {"title", "details", "due_at", "status", "goal_id"},
                   "goals": {"title", "details", "status"},
                   "reminders": {"acknowledged"}, "memories": {"content"}}[kind]
        values = {k: v for k, v in values.items() if k in allowed}
        if not values:
            raise ValueError("No fields to update")
        if "status" in values and values["status"] not in ({"open", "done"} if kind == "tasks" else {"active", "done"}):
            raise ValueError("Invalid status")
        for key in ("title", "content"):
            if key in values and (not isinstance(values[key], str) or not values[key].strip() or len(values[key]) > 4000):
                raise ValueError(f"{key.title()} must contain between 1 and 4,000 characters")
        if "due_at" in values:
            values["due_at"] = due_date(values["due_at"])
        count = self.execute(f"UPDATE {kind} SET {','.join(k+'=?' for k in values)} WHERE id=?", (*values.values(), item_id))
        if not count:
            raise ValueError("Entry not found")
        return self.row(f"SELECT * FROM {kind} WHERE id=?", (item_id,))

    def pending(self, conversation_id=None):
        query = "SELECT * FROM approvals WHERE status='pending'"
        args = ()
        if conversation_id:
            query += " AND conversation_id=?"
            args = (conversation_id,)
        items = self.rows(query + " ORDER BY created_at", args)
        for item in items:
            item["arguments"] = json.loads(item["arguments"])
            item["preview"] = json.loads(item["preview"])
        return items

    def recover(self):
        self.execute("UPDATE workflows SET status='interrupted',detail='JARVIS restarted; inspect results before retrying',updated_at=? WHERE status='running'", (now(),))
        self.execute("UPDATE action_log SET status='interrupted',updated_at=? WHERE status='running'", (now(),))
        # Never repeat a command after a crash: its side effect may already have happened.
        running = self.rows("SELECT * FROM approvals WHERE status='running'")
        for item in running:
            result = "JARVIS restarted during this action. Its outcome is unknown; inspect your computer before retrying."
            self.execute("UPDATE approvals SET status='interrupted',result=? WHERE id=?", (result, item["id"]))
            self.message(item["conversation_id"], {"role": "tool", "tool_call_id": item["tool_call_id"], "content": result})
        # A crash can also happen between a model tool call and its registration.
        for conversation in self.rows("SELECT id FROM conversations"):
            history = self.model_history(conversation["id"])
            answered = {m.get("tool_call_id") for m in history if m["role"] == "tool"}
            pending = {a['tool_call_id'] for a in self.rows("SELECT tool_call_id FROM approvals WHERE conversation_id=? AND status IN ('pending','deferred')",(conversation['id'],))}
            for message in history:
                for call in message.get("tool_calls", []):
                    if call["id"] not in answered | pending:
                        self.message(conversation["id"], {"role": "tool", "tool_call_id": call["id"],
                            "content": "JARVIS restarted before this tool result was saved. The outcome is unknown; inspect current state before retrying."})
