"""The kernel: pipeline template, policy types, audit, quota state, config.

Everything a guard needs from the trust boundary that is *not itself* GitLab/git-specific:
the pipeline template method (core.guard.Guard.handle), shared policy value types
(core.model), typed audit event, durable quota state.

No GitLab/git vocabulary lives here (core.config.Config is the one documented exception).
"""

from __future__ import annotations

from .audit import AUDIT_SCHEMA_VERSION, AuditEvent, AuditLog, build_event, redact
from .config import Config, ConfigError, normalize_project
from .config_load import from_env
from .guard import (
    Guard,
    action_gate,
    criticality_gate,
    kernel_gates,
    project_gate,
    write_credential_gate,
)
from .model import Decision, Intent, StateView, TokenKind
from .path_template import compile_template
from .state import CURRENT_SCHEMA_VERSION, SchemaError, State

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "CURRENT_SCHEMA_VERSION",
    "AuditEvent",
    "AuditLog",
    "Config",
    "ConfigError",
    "Decision",
    "Guard",
    "Intent",
    "SchemaError",
    "State",
    "StateView",
    "TokenKind",
    "action_gate",
    "build_event",
    "compile_template",
    "criticality_gate",
    "from_env",
    "kernel_gates",
    "normalize_project",
    "project_gate",
    "redact",
    "write_credential_gate",
]
