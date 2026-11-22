"""Modes on shift+tab: normal asks before edits and commands, accept edits only before commands, plan blocks edits
and all but read-only commands until the model's plan is approved through exit_plan_mode."""

MODES = ["normal", "accept edits", "plan"]
MODE_LINES = {"normal": "? shift+tab to cycle modes", "accept edits": "⏵⏵ accept edits on (shift+tab to cycle)",
              "plan": "⏸ plan mode on (shift+tab to cycle)"}
PLAN_NOTE = ("<system-reminder>Plan mode is on: don't change any files. Research with read and read-only bash, "
             "then call exit_plan_mode with your plan for the user to approve.</system-reminder>")
PLAN_DESCRIPTION = ("Only in plan mode: present your finished plan, in markdown, for the user to approve. "
                    "Once approved you can change files.")


def install(api) -> None:
    """Keep the mode on the agent, tell the model about plan mode on every request while it's on, and add exit_plan_mode."""

    class ModesAgent(api.Agent):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.mode = "normal"

        def request_messages(self) -> list[dict]:
            messages = super().request_messages()
            if self.mode != "plan":
                return messages
            # added to a copy of the last user message, so the saved history stays clean
            last = max(i for i, m in enumerate(messages) if m["role"] == "user")
            note = {**messages[last], "content": messages[last]["content"] + "\n\n" + PLAN_NOTE}
            return [*messages[:last], note, *messages[last + 1:]]

        def review_plan(self, plan: str) -> str:
            """Ask whether to go ahead with the plan shown above, and leave plan mode if so."""
            if self.mode != "plan":
                return "Plan mode is off, go ahead."
            options = ["Yes, and auto-accept edits", "Yes, and manually approve edits", "No, keep planning (esc)"]
            choice = self.ask("Would you like to proceed?", options)
            if choice in (None, 2):
                self.stop()
                return "The user wants to keep planning. Wait for their feedback."
            self.mode = "accept edits" if choice == 0 else "normal"
            return "The user approved the plan. Start implementing it now."

    def exit_plan_mode(cwd, stop, plan: str) -> str:
        """Ask the user to approve the plan."""
        return api.agent.review_plan(plan)

    api.Agent = ModesAgent
    api.add_tool(exit_plan_mode, PLAN_DESCRIPTION, {"plan": {"type": "string"}}, ["plan"])


class ModesMixin:
    def action_cycle_mode(self) -> None:
        self.agent.mode = MODES[(MODES.index(self.agent.mode) + 1) % len(MODES)]
        self.update_status()
