"""Declared, durable provenance for local Yigdesk agent writers.

The public demo deliberately does not claim authentication or signing.  Identity
is bound by the MCP process configuration, validated here, and copied into every
agent-authored ledger operation so it remains inspectable after the process exits.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping


IDENTITY_VERSION = "yigdesk-agent-identity/v1"
IDENTITY_ASSURANCE = "local_config_declared"
_PROCESS_INSTANCE_ID = f"mcp-{uuid.uuid4()}"
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_IDENTITY_FIELDS = {
    "version", "agent_id", "run_id", "instance_id", "profile", "model",
    "prompt_revision", "skills_revision", "memory_revision", "assurance",
}


class AgentIdentityError(ValueError):
    pass


def _value(
    environ: Mapping[str, str], key: str, default: str, *, slug: bool = False
) -> str:
    value = environ.get(key, default)
    if not isinstance(value, str) or not value or len(value) > 160:
        raise AgentIdentityError(f"{key} must be a non-empty string up to 160 characters")
    if any(ord(char) < 32 for char in value):
        raise AgentIdentityError(f"{key} contains control characters")
    if slug and not _SLUG.fullmatch(value):
        raise AgentIdentityError(f"{key} must be a safe identifier")
    return value


@dataclass(frozen=True)
class AgentIdentity:
    version: str
    agent_id: str
    run_id: str
    instance_id: str
    profile: str
    model: str
    prompt_revision: str
    skills_revision: str
    memory_revision: str
    assurance: str = IDENTITY_ASSURANCE

    @property
    def actor(self) -> str:
        return f"agent:{self.agent_id}"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def identity_from_env(environ: Mapping[str, str] | None = None) -> AgentIdentity:
    """Return the identity declared by this MCP process configuration.

    A generated process instance is used only when the caller did not provide a
    run id.  It is stable for the lifetime of the MCP process and is persisted in
    the ledger; role configs should set the stable ``agent_id`` and revisions.
    """

    values = os.environ if environ is None else environ
    agent_id = _value(values, "YIGDESK_AGENT_ID", "mcp", slug=True)
    instance_id = _value(
        values, "YIGDESK_AGENT_INSTANCE_ID", _PROCESS_INSTANCE_ID, slug=True
    )
    return AgentIdentity(
        version=IDENTITY_VERSION,
        agent_id=agent_id,
        run_id=_value(values, "YIGDESK_AGENT_RUN_ID", instance_id, slug=True),
        instance_id=instance_id,
        profile=_value(values, "YIGDESK_AGENT_PROFILE", agent_id, slug=True),
        model=_value(values, "YIGDESK_AGENT_MODEL", "unspecified"),
        prompt_revision=_value(
            values, "YIGDESK_PROMPT_REVISION", "unversioned", slug=True
        ),
        skills_revision=_value(
            values, "YIGDESK_SKILLS_REVISION", "unversioned", slug=True
        ),
        memory_revision=_value(
            values, "YIGDESK_MEMORY_REVISION", "none", slug=True
        ),
    )


def validate_agent_identity(value: Any) -> dict[str, str]:
    """Validate the exact v1 envelope before it enters the append-only ledger."""

    if isinstance(value, AgentIdentity):
        value = value.to_dict()
    if not isinstance(value, dict) or set(value) != _IDENTITY_FIELDS:
        raise AgentIdentityError("agent identity must contain exactly the v1 fields")
    if value.get("version") != IDENTITY_VERSION:
        raise AgentIdentityError("unsupported agent identity version")
    if value.get("assurance") != IDENTITY_ASSURANCE:
        raise AgentIdentityError("unsupported agent identity assurance")
    checked = dict(value)
    for field in _IDENTITY_FIELDS - {"version", "assurance"}:
        checked[field] = _value(checked, field, "", slug=field != "model")
    return checked
