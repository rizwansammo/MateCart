"""The only place the Agent starts processes (D-119).

- argv lists only, never a shell; the executable must be in ``ALLOWED_EXECUTABLES``;
- bounded run time and bounded, redacted output (secrets never reach logs or Control);
- a ``FakeRunner`` records argv for tests and dry runs, so no test needs Docker or SSH.
"""

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

ALLOWED_EXECUTABLES = frozenset({"docker", "ssh"})
MAX_OUTPUT = 64 * 1024
SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(password|secret|token|key)=\S+"), r"\1=<redacted>"),
    (re.compile(r"\b(postgres(?:ql)?|redis)://[^@\s]*@"), r"\1://<redacted>@"),
)


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class CommandRefused(RuntimeError):
    """An argv outside the allowlist was requested."""


@dataclass(frozen=True)
class Completed:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 600,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
        stdout_path: str | None = None,
    ) -> Completed:
        raise NotImplementedError


def check_argv(argv: Sequence[str]) -> tuple[str, ...]:
    items = tuple(str(item) for item in argv)
    if not items or items[0] not in ALLOWED_EXECUTABLES:
        raise CommandRefused(f"Refusing to run {items[:1]!r}: not an allowlisted executable.")
    if any("\0" in item or "\n" in item for item in items):
        raise CommandRefused("Refusing an argument containing a newline or NUL.")
    return items


class SubprocessRunner(Runner):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 600,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
        stdout_path: str | None = None,
    ) -> Completed:
        items = check_argv(argv)
        process_env = {key: value for key, value in os.environ.items() if key in SAFE_ENV}
        if env:
            process_env.update(env)
        try:
            if stdout_path is not None:
                # Large binary streams (database dumps) go straight to a 0600 file.
                fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    result = subprocess.run(
                        items,
                        input=stdin,
                        stdout=handle,
                        stderr=subprocess.PIPE,
                        timeout=timeout,
                        env=process_env,
                        check=False,
                    )
                stdout = ""
            else:
                result = subprocess.run(
                    items,
                    input=stdin,
                    capture_output=True,
                    timeout=timeout,
                    env=process_env,
                    check=False,
                )
                stdout = result.stdout.decode("utf-8", "replace")[-MAX_OUTPUT:]
        except subprocess.TimeoutExpired:
            return Completed(items, 124, "", "timed out")
        except FileNotFoundError:
            return Completed(items, 127, "", f"{items[0]} is not installed")
        stderr = redact(result.stderr.decode("utf-8", "replace")[-MAX_OUTPUT:])
        return Completed(items, result.returncode, stdout, stderr)


# Environment passed to child processes: enough for Docker to find its socket/config.
SAFE_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        # Windows hosts (local acceptance only): where the Docker CLI finds its config and plugins.
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "TEMP",
        "TMP",
    }
)


@dataclass
class FakeRunner(Runner):
    """Records argv; answers through ``responder`` (default: success with empty output)."""

    responder: Callable[[tuple[str, ...]], Completed | None] | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 600,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
        stdout_path: str | None = None,
    ) -> Completed:
        items = check_argv(argv)
        self.calls.append(items)
        self.envs.append(env)
        answer = self.responder(items) if self.responder else None
        if stdout_path is not None and (answer is None or answer.ok):
            fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(b"fake-dump:" + " ".join(items).encode())
        return answer or Completed(items, 0, "", "")
