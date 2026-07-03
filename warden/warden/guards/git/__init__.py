"""The git Smart-HTTP guard (§03.3): pktline parsing, ref-policing, and the
three git routes. Forge-agnostic on purpose — no GitLab vocabulary appears
anywhere in this package; the credential injection and upstream URL shape it
borrows from :mod:`warden.guards.gitlab_api` are the one honest exception this
migration step leaves in place (see ``guard.py``'s module docstring).
"""

from __future__ import annotations
