from pathlib import Path

from modules import config
from modules.agent import Agent
from modules.tui import Chat


def main() -> None:
    Chat(Agent(config.load(), Path.cwd())).run()


if __name__ == "__main__":
    main()
