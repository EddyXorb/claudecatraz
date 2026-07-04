"""Action catalog, git side: the two git Smart-HTTP transport verbs.

§09 §1.2/§5: ``git.fetch`` covers ``advertise`` (upload discovery) plus
``upload-pack`` — a read; ``git.push`` covers ``advertise`` (push discovery)
plus ``receive-pack`` — a write. :func:`action_for_git_operation` maps a
:class:`~.intent.GitIntent`'s ``operation``/``service`` pair onto one of the
two. Defined and tested only here — the git-guard gate that denies a request
whose action is missing from the host's effective actions is a later step
(§09 04); nothing here wires into :class:`~.guard.GitGuard` yet.
"""

from __future__ import annotations

GIT_FETCH = "git.fetch"
GIT_PUSH = "git.push"

_UPLOAD_SERVICE = "git-upload-pack"
_RECEIVE_SERVICE = "git-receive-pack"


def action_for_git_operation(operation: str, service: str = _UPLOAD_SERVICE) -> str:
    """Map a git Smart-HTTP operation (+ ``service``) to its action ID.

    ``operation`` is :attr:`~.intent.GitIntent.operation`: one of
    ``"advertise"``, ``"upload-pack"``, ``"receive-pack"``. ``service`` is
    only meaningful for ``"advertise"`` — the initial ref-discovery request,
    which carries a ``?service=`` query param telling fetch discovery apart
    from push discovery; it is ignored for the other two operations, which
    already commit to a direction on the wire regardless of ``service``.

    Raises ``ValueError`` on an operation/service outside this closed
    vocabulary — a programmer error (an unrecognised
    :class:`~.intent.GitIntent` shape), never a config problem.
    """
    if operation == "upload-pack":
        return GIT_FETCH
    if operation == "receive-pack":
        return GIT_PUSH
    if operation == "advertise":
        if service == _UPLOAD_SERVICE:
            return GIT_FETCH
        if service == _RECEIVE_SERVICE:
            return GIT_PUSH
        raise ValueError(f"advertise: unknown service {service!r}")
    raise ValueError(f"unknown git operation {operation!r}")
