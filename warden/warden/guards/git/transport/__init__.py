"""git Smart-HTTP guard: pktline parsing, ref recognizers, three git routes.

Serves every git-namespace endpoint type — a "plain" endpoint is just this
guard alone. Never imports the gitlab guard.
"""

from __future__ import annotations
