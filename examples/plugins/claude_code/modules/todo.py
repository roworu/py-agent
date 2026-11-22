"""todo_write tool: the model keeps a checklist, the ui draws it from the call's arguments."""

DESCRIPTION = ("Keep a todo list for multi-step tasks, shown to the user. Send the whole list each time; "
               "keep exactly one item in_progress and mark items completed as soon as they're done.")
PROPS = {"todos": {"type": "array", "items": {"type": "object", "properties": {
    "content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
    "required": ["content", "status"]}}}


def todo_write(cwd, stop, todos: list) -> str:
    """Nothing to do here, the list lives in the call's arguments."""
    return "todos updated"


def install(api) -> None:
    """Register the tool."""
    api.add_tool(todo_write, DESCRIPTION, PROPS, ["todos"])
