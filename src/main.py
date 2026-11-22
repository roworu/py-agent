import argparse
import os
import ssl
import sys
from pathlib import Path

from modules import config, files, plugins
from modules.llm import ApiError


class PrintEvents:
    """Agent events for -p: the answer to stdout, tool calls and retries to stderr, questions declined."""

    def __init__(self) -> None:
        # whether stdout has text not yet ended with a newline
        self.open = False

    def text(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        self.open = not text.endswith("\n")

    def reasoning(self, text: str) -> None:
        pass

    def tool_call(self, id: str, name: str, arguments: str) -> None:
        if self.open:
            print()
            self.open = False
        print(f"→ {name} {arguments}", file=sys.stderr)

    def tool_result(self, id: str, result: str) -> None:
        pass

    def retry(self, delay: float, error: str) -> None:
        print(f"retrying in {delay:g}s: {error}", file=sys.stderr)

    def compacted(self) -> None:
        print("context nearly full, compacted the conversation", file=sys.stderr)

    def ask(self, question: str, options: list[str], body=None) -> int | None:
        return None


def use_system_certs() -> None:
    """Point openssl at the system ca bundle when its built-in path is missing."""
    # the binary's openssl looks where the build machine (ubuntu) keeps certs, e.g. fedora has them elsewhere
    if "SSL_CERT_FILE" in os.environ or Path(ssl.get_default_verify_paths().openssl_cafile).is_file():
        return
    for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
        if Path(path).is_file():
            os.environ["SSL_CERT_FILE"] = path
            return


def main() -> None:
    use_system_certs()
    parser = argparse.ArgumentParser(prog="py-agent")
    parser.add_argument("-c", "--continue", dest="resume", action="store_true",
                        help="continue the last session in this folder")
    parser.add_argument("-p", "--print", dest="prompt", metavar="PROMPT",
                        help="answer one message without the ui, print the answer and exit")
    args = parser.parse_args()
    # plugins go first so their tools, commands and replaced classes are used from the start
    api = plugins.Api(config.load(), Path.cwd())
    plugins.load(api)
    api.agent = api.Agent(api.config, api.cwd)
    if args.resume and (sessions := sorted(api.agent.sessions.glob("*.jsonl"))):
        api.agent.resume(sessions[-1])
    if args.prompt is not None:
        events = PrintEvents()
        try:
            api.agent.run(files.message(api.agent, args.prompt), events)
        except ApiError as e:
            sys.exit(f"error: {e}")
        if events.open:
            print()
        return
    api.chat = api.Chat(api.agent)
    api.chat.run()


if __name__ == "__main__":
    main()
