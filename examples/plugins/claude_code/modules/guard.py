"""Read before edit: write and edit refuse files the model hasn't read, or that changed since it read them."""

from pathlib import Path


def resolve(cwd: Path, path: str) -> Path:
    return (cwd / Path(path).expanduser()).resolve()


def install(api) -> None:
    """Swap in an Agent subclass that remembers what was read and checks it before write/edit; forgotten on /clear and /compact."""

    class GuardAgent(api.Agent):
        def reset(self) -> None:
            super().reset()
            # resolved path -> mtime when the model last saw it; a new or compacted conversation hasn't seen any file
            self.seen: dict[Path, int] = {}

        def before_tool(self, name: str, args: dict) -> str | None:
            if name in ("write", "edit"):
                path = str(args.get("path", ""))
                p = resolve(self.cwd, path)
                # new files need no read
                if p.is_file() and p not in self.seen:
                    return f"Error: read {path} before changing it"
                if p.is_file() and p.stat().st_mtime_ns != self.seen[p]:
                    return f"Error: {path} changed since you read it, read it again"
            return super().before_tool(name, args)

        def after_tool(self, name: str, args: dict, result: str) -> str:
            p = resolve(self.cwd, str(args.get("path", "")))
            if name in ("read", "write", "edit") and not result.startswith("Error:") and p.is_file():
                self.seen[p] = p.stat().st_mtime_ns
            return super().after_tool(name, args, result)

    api.Agent = GuardAgent
