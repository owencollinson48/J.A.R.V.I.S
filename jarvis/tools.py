import json


def function(name, description, properties=None, required=None):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}}


S = {"type": "string"}
TOOLS = [
    function("list_files", "List a folder on the user's computer. Relative paths start in the first access folder.", {"path": S}),
    function("search_files", "Find files by name, optionally search inside text files. Results are bounded; narrow directory for large searches.", {"query": S, "directory": S, "search_content": {"type": "boolean"}}, ["query"]),
    function("read_file", "Read a text or Word .docx document. Check truncated before attempting a rewrite.", {"path": S}, ["path"]),
    function("write_document", "Create or replace a text/.docx document AFTER user approval. Read existing files first. Full replacement; .docx formatting is replaced, original backed up. Never rewrite a truncated document.", {"path": S, "content": S}, ["path", "content"]),
    function("create_folder", "Create a folder after user approval.", {"path": S}, ["path"]),
    function("move_file", "Move or rename a file on the same drive without overwriting, after approval.", {"source": S, "destination": S}, ["source", "destination"]),
    function("open_file", "Open an existing file or folder in its default desktop app after approval.", {"path": S}, ["path"]),
    function("open_application", "Launch a Windows app after approval. Built-ins: notepad, calculator, explorer, paint; or exact executable name/path.", {"name": S, "arguments": {"type": "array", "items": S}}, ["name"]),
    function("run_command", "Run a command after explicit approval, with a 30 second limit. Provide arguments as an array, not shell text. Only for the user's requested task. Never send emails, post messages, purchase or publish.", {"arguments": {"type": "array", "items": S}, "directory": S}, ["arguments"]),
    function("create_task", "Add a task; due_at is ISO 8601 WITH timezone, optional goal_id associates it with a goal.", {"title": S, "details": S, "due_at": S, "goal_id": S}, ["title"]),
    function("update_task", "Update task status (open or done), title, details or due date.", {"id": S, "status": {"type": "string", "enum": ["open", "done"]}, "title": S, "details": S, "due_at": S}, ["id"]),
    function("create_goal", "Save a goal and optional details. Then create tasks associated with its returned id.", {"title": S, "details": S}, ["title"]),
    function("update_goal", "Mark a goal active or done, or edit its title/details.", {"id": S, "status": {"type": "string", "enum": ["active", "done"]}, "title": S, "details": S}, ["id"]),
    function("set_reminder", "Schedule a reminder. due_at must be an ISO 8601 datetime WITH timezone. Ask if the intended time is ambiguous. JARVIS must stay running to deliver.", {"title": S, "due_at": S}, ["title", "due_at"]),
    function("list_planner", "List the user's tasks, goals and reminders."),
    function("remember", "Save a personal fact or preference explicitly shared by the user for future conversations. Do not save secrets or guesses.", {"content": S}, ["content"]),
    function("search_memory", "Search saved facts and previous conversations for relevant context.", {"query": S}, ["query"]),
]
APPROVAL_TOOLS = {"write_document", "create_folder", "move_file", "open_file", "open_application", "run_command"}


class ToolRunner:
    def __init__(self, store, computer):
        self.store, self.computer = store, computer

    def validate(self, name, args):
        schema = next((tool["function"]["parameters"] for tool in TOOLS if tool["function"]["name"] == name), None)
        if not schema or not isinstance(args, dict):
            raise ValueError("Unknown tool or invalid arguments")
        if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
            raise ValueError("Missing or unsupported tool arguments")
        for key, value in args.items():
            spec = schema["properties"][key]
            typ = {"string": str, "boolean": bool, "array": list}[spec["type"]]
            if not isinstance(value, typ) or (isinstance(value, list) and any(not isinstance(v, str) for v in value)):
                raise ValueError(f"Invalid value for {key}")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"Invalid value for {key}")

    def run(self, name, args, preview=None, conversation_id=None):
        self.validate(name, args)
        if name in APPROVAL_TOOLS:
            if preview is None:
                raise ValueError("Computer actions require approval")
            args = dict(args)
            if name in {"write_document", "move_file"}:
                args["fingerprint"] = preview.get("fingerprint")
            return getattr(self.computer, name)(**args)
        if name in {"list_files", "search_files", "read_file"}:
            return getattr(self.computer, name)(**args)
        if name in {"create_task", "create_goal", "set_reminder", "remember"}:
            kind = {"create_task": "tasks", "create_goal": "goals", "set_reminder": "reminders", "remember": "memories"}[name]
            return self.store.create_item(kind, args)
        if name in {"update_task", "update_goal"}:
            args = dict(args)
            item_id = args.pop("id")
            return self.store.update_item("tasks" if name == "update_task" else "goals", item_id, args)
        if name == "search_memory":
            return self.store.search_memory(args["query"], conversation_id)
        if name == "list_planner":
            return {kind: self.store.rows(f"SELECT * FROM {kind} ORDER BY created_at DESC LIMIT 100") for kind in ["tasks", "goals", "reminders"]}
        raise ValueError("Unknown tool")
