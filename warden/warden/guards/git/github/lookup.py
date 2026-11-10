"""Upstream lookups that decide whose a pull request or CI run is.

Read-token GETs with a short-TTL cache (performance only, never security);
keyed by canonical host so two hosts never share an entry. Every failure
answers None or UNKNOWN, which denies."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional
from urllib.parse import quote

import httpx

from ....core.config import Config
from ....core.model import TokenKind
from ....core.transport import Upstream, UpstreamRouter
from .intent import IssueKind


def repo_path(project: str) -> str:
    """repos/{owner}/{repo} with each segment percent-encoded."""
    owner, _, repo = project.partition("/")
    return f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}"


def _repo_id(obj: Any) -> Optional[int]:
    value = obj.get("id") if isinstance(obj, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def pull_is_agents(cfg: Config, host: str, pull: Any) -> bool:
    """A pull request is the agent's iff its head branch is in the namespace
    and its head repository is the base repository itself — a fork's
    same-named branch is someone else's, a deleted fork is no one's."""
    if not isinstance(pull, dict):
        return False
    head, base = pull.get("head") or {}, pull.get("base") or {}
    head_id, base_id = _repo_id(head.get("repo")), _repo_id(base.get("repo"))
    ref = head.get("ref")
    return (
        head_id is not None
        and head_id == base_id
        and isinstance(ref, str)
        and cfg.in_branch_namespace(host, ref)
    )


def run_is_agents(cfg: Config, host: str, run: Any) -> bool:
    """Same rule for a workflow run: its head branch in the namespace, its
    head repository the repository itself."""
    if not isinstance(run, dict):
        return False
    head_id = _repo_id(run.get("head_repository"))
    branch = run.get("head_branch")
    return (
        head_id is not None
        and head_id == _repo_id(run.get("repository"))
        and isinstance(branch, str)
        and cfg.in_branch_namespace(host, branch)
    )


class GithubLookups:
    def __init__(
        self, router: UpstreamRouter, cfg: Config, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._router = router
        self._cfg = cfg
        self._clock = clock
        self._ttl = 30.0
        self._cache: dict[tuple[str, str], tuple[object, float]] = {}

    async def _get(self, host: str, path: str) -> tuple[Optional[str], Optional[Any], int]:
        """(canonical host, JSON body or None, status); status 0 when unresolved."""
        upstream: Optional[Upstream] = self._router.resolve(host)
        canonical = self._cfg.resolve_target_host(host)
        if upstream is None or canonical is None:
            return None, None, 0
        try:
            resp = await upstream.get_json(path, TokenKind.READ)
        except httpx.HTTPError:
            return canonical, None, 0
        if resp.status_code != 200:
            return canonical, None, resp.status_code
        try:
            return canonical, resp.json(), 200
        except ValueError:
            return canonical, None, 0

    def _cached(self, key: tuple[str, str]) -> Optional[object]:
        hit = self._cache.get(key)
        return hit[0] if hit is not None and hit[1] > self._clock() else None

    def _store(self, key: tuple[str, str], value: object) -> None:
        self._cache[key] = (value, self._clock() + self._ttl)

    async def issue_kind(self, host: str, project: str, number: int) -> IssueKind:
        """PULL iff the issue carries a pull_request key; a 404 or any other
        failure is UNKNOWN — it may hide a pull request the token can't see."""
        path = f"{repo_path(project)}/issues/{number}"
        key = (self._cfg.normalize_host(host), path)
        cached = self._cached(key)
        if isinstance(cached, IssueKind):
            return cached
        canonical, body, _ = await self._get(host, path)
        if canonical is None or not isinstance(body, dict):
            return IssueKind.UNKNOWN
        kind = IssueKind.PULL if "pull_request" in body else IssueKind.ISSUE
        self._store(key, kind)
        return kind

    async def _owned(
        self, host: str, path: str, rule: Callable[[Config, str, Any], bool]
    ) -> Optional[bool]:
        key = (self._cfg.normalize_host(host), path)
        cached = self._cached(key)
        if isinstance(cached, bool):
            return cached
        canonical, body, _ = await self._get(host, path)
        if canonical is None or body is None:
            return None
        ok = rule(self._cfg, canonical, body)
        self._store(key, ok)
        return ok

    async def pull_in_namespace(self, host: str, project: str, number: int) -> Optional[bool]:
        return await self._owned(host, f"{repo_path(project)}/pulls/{number}", pull_is_agents)

    async def run_in_namespace(self, host: str, project: str, run_id: int) -> Optional[bool]:
        path = f"{repo_path(project)}/actions/runs/{run_id}"
        return await self._owned(host, path, run_is_agents)

    async def job_in_namespace(self, host: str, project: str, job_id: int) -> Optional[bool]:
        """A job is the agent's iff its run is."""
        _, job, _ = await self._get(host, f"{repo_path(project)}/actions/jobs/{job_id}")
        run_id = job.get("run_id") if isinstance(job, dict) else None
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            return None
        return await self.run_in_namespace(host, project, run_id)
