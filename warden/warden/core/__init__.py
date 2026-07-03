"""The kernel: pipeline template, policy types, rule registry, audit, quota state, config.

Everything a guard needs from the trust boundary that is *not itself* GitLab/git-specific:
the pipeline template method (:meth:`core.guard.Guard.handle`), shared policy value types
(:mod:`core.model`), the rule registry, capability vocabulary, typed audit event, durable quota state.

No GitLab/git vocabulary lives here (``core.config.Config`` is the one documented exception).
"""

from __future__ import annotations

from .audit import AUDIT_SCHEMA_VERSION, AuditEvent, AuditLog, build_event, redact
from .capabilities import FORBIDDEN, Capability, forbidden_check
from .config import Config, ConfigError, normalize_project
from .config_load import from_env
from .guard import (
    Guard,
    kernel_gates,
    mode_gate_off,
    mode_gate_writes,
    project_gate,
)
from .model import Decision, Intent, StateView, TokenKind
from .path_template import compile_template
from .rules import (
    GITLAB_NAMESPACE,
    KERNEL_NAMESPACE,
    R0,
    R1,
    R2,
    R3,
    R4,
    R5,
    R6,
    RULES,
    MetaRule,
    RuleDef,
    qualify,
    rule,
)
from .state import CURRENT_SCHEMA_VERSION, SchemaError, State

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "CURRENT_SCHEMA_VERSION",
    "FORBIDDEN",
    "GITLAB_NAMESPACE",
    "KERNEL_NAMESPACE",
    "R0",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "RULES",
    "AuditEvent",
    "AuditLog",
    "Capability",
    "Config",
    "ConfigError",
    "Decision",
    "Guard",
    "Intent",
    "MetaRule",
    "RuleDef",
    "SchemaError",
    "State",
    "StateView",
    "TokenKind",
    "build_event",
    "compile_template",
    "forbidden_check",
    "from_env",
    "kernel_gates",
    "mode_gate_off",
    "mode_gate_writes",
    "normalize_project",
    "project_gate",
    "qualify",
    "redact",
    "rule",
]
