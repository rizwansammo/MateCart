"""SSH bootstrap path for future, owner-approved installations (D-125).

Not used in Phase 17: no SSH connection is made by tests or acceptance. Safety properties:

- the target's host key must be supplied out of band and match the expected SHA256 fingerprint
  recorded for the Server; a pinned known_hosts file is used with StrictHostKeyChecking=yes, so an
  unknown or changed key is always refused (never trust-on-first-use);
- BatchMode (no password prompts, no root password collection), key authentication only;
- the remote command is fixed (``sudo -n /bin/bash -s -- <mode>``); the bootstrap script and the
  Agent bundle are sent on stdin, never interpolated into a remote shell command line;
- ``--execute`` is required for anything other than printing the plan.
"""

import base64
import hashlib
import io
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from . import validation

HOST_KEY = re.compile(
    r"^(ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|rsa-sha2-512|"
    r"ssh-rsa) ([A-Za-z0-9+/]+={0,3})(?: .*)?$"
)
HOSTNAME = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MODES = frozenset({"preflight", "install"})


def fingerprint(public_key_line: str) -> str:
    match = HOST_KEY.fullmatch(public_key_line.strip())
    if match is None:
        raise validation.ValidationError("Unsupported or malformed SSH host key.")
    blob = base64.b64decode(match.group(2))
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


@dataclass(frozen=True)
class Target:
    host: str
    user: str
    port: int
    host_key: str
    expected_fingerprint: str

    def validate(self) -> "Target":
        if not HOSTNAME.fullmatch(self.host) or self.host.startswith("-"):
            raise validation.ValidationError("Invalid target host.")
        if not USER.fullmatch(self.user):
            raise validation.ValidationError("Invalid SSH user.")
        if not 1 <= self.port <= 65535:
            raise validation.ValidationError("Invalid SSH port.")
        if fingerprint(self.host_key) != self.expected_fingerprint.strip():
            raise validation.ValidationError(
                "The host key does not match the expected fingerprint; refusing to connect."
            )
        return self


def known_hosts_line(target: Target) -> str:
    match = HOST_KEY.fullmatch(target.host_key.strip())
    if match is None:
        raise validation.ValidationError("Unsupported or malformed SSH host key.")
    name = target.host if target.port == 22 else f"[{target.host}]:{target.port}"
    return f"{name} {match.group(1)} {match.group(2)}\n"


def ssh_argv(target: Target, *, known_hosts: Path, identity: Path, mode: str) -> list[str]:
    if mode not in MODES:
        raise validation.ValidationError("Mode must be preflight or install.")
    target.validate()
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "ConnectTimeout=15",
        "-i",
        str(identity),
        "-p",
        str(target.port),
        "--",
        f"{target.user}@{target.host}",
        "sudo",
        "-n",
        "/bin/bash",
        "-s",
        "--",
        mode,
    ]


def bundle(agent_root: Path) -> bytes:
    """bootstrap.sh followed by a base64 tar of the Agent package and templates (stdin payload)."""
    script = (agent_root / "installer" / "bootstrap.sh").read_bytes()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for folder in ("matecart_agent", "templates", "installer"):
            for path in sorted((agent_root / folder).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.add(path, arcname=str(path.relative_to(agent_root)))
    encoded = base64.b64encode(buffer.getvalue())
    return script + b"\n__MATECART_AGENT_BUNDLE__\n" + encoded + b"\n"
