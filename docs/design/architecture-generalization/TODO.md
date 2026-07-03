# TODO

Offene Punkte, in loser Reihenfolge:

- wenn man catraz einem ordner hinzufügt will man meist, dass die repos darin wenn sie gitlab-repos sind zu den allowed projects gehören. Es sollte eine mechanik geben, die das abfragt. Weiters sollte es ein command geben, dass nach init aufrufbar ist um die allowed projects zu ergänzen
- es ist irgendwie noch nicht ausgereift, dass man mit "run" claude startet, aber mit "up --remote"  remote control startet, obwohl beide dinge auf einer ebene liegen. Das muss besser werden und vielleicht indem man profile einführt, die als asset mit ins catraz home verzeichnis kommen und die man mit "run [profile_name]" startet, wobei es zwei vorinstallierte gibt: einfach claude starten (profil: claude) bzw. remote-control starten (profil: claude-remote) bzw. einfach eine shell (profil: shell), damit fällt dann auch das shell subcommand weg
- es ist fraglich, ob es überhaupt relevant ist up/down als cli interface zu haben, von außen betrachtet will man ja nur claude starten in einer gesicherten art und weise und es leuchtet ein, dass man das einrichten muss (init), aber up und down ist zuviel detail, eventuell weglassen und beim ersten run einfach up machen intern, und dann den forwarder/warden laufen lassen (eventuell ein command belassen zum beenden)
- es fehlt noch die Möglichkeit einen container zu detachen bzw. reattachen und eine liste der aktiven container des aktuellen catraz-repos zu sehen
- es ist ungünstig, dass im claude dev env diese seltsame mechanik mit "insteadof" ist, nur um zum warden weiterzuleiten. Das sollte doch eigentlich versteckt sein aus sicht des dev containers, so dass es auch keine unterscheidung mehr mit "insteadof" geben muss um im hostcontainer zu funktionieren. die struktur des forwarders/warden nicht eine parallele, sondern sequenzielle sein. Erst zum forwarder, der dann gewisse urls zum warden weiterleitet und den rest einfach rausschickt, so dass der load beim warden nicht so hoch ist und nur wenige anfragen doppelt durchgeschleift werden muss. Das hat den Vorteil von defense in depth: wenn man eigentlich gitlab abschaten wollte und der gitlab mode "off" ist, aber der forwarder aus irgendwelchen gründen nicht stoppt, tut es hoffentlich der warden
- der warden ist ein monolith, aber eigentlich sind hier zwei sachen vermischt: git und gitlab. Es sollte irgendwie eine abstraktion geben für einen "adapter" den man dann an den warden anflanschen kann und der die betroffenen routen enthält und die nachgelagerte verarbeitung, so dass es einfacher wird einfach z.b. ein github-adapter hinzuzufügen, in dem man einfach die adapterclasse für github ableitet und mit logik füllt und den adapter in eine liste einreiht, wo alle adapter drin sind die aktiv sein sollen. Dann kann man von außen auch sehr leicht gewisse adapter an/abschalten
- squid als lösung für den forwarder sollte anderen lösungen gegenübergestellt werden, vielleicht gibts was moderneres/wartbareres mit schönerer config-syntax (traefik oder caddy?)
- wenn wir mehrere container laufen haben mit dev-envs und dazwischen die variables ändern im .catraz sollen alte container keine verhaltensänderung aufweisen plötzlich. Ich glaube das ist schon sichergestellt, weil der warden die config einmal liest und dann erst wieder wenn man neu startet. die offene frage ist, wie ein "refresh" in der cli gelöst werden sollte wenn es passiert, dass man über laufende env-container gewarnt wird und wie das commando heißt (vielleicht reinit, oder einfach init dafür nehmen)?
- das `prune` command ist meiner meinung nach nicht nötig und bläht die cli auf.
- es wäre schön, die container-logs im logs-ordner in .catraz zu haben.

---

## Triage & next steps

The loose points above, distilled. Each got a codebase check (CLI, Warden, proxy
topology) before a verdict. Grouped by topic, then re-ordered into a single
worklist from smallest to biggest, by dependency where size doesn't decide it.
Labels: **P1**…**P8** map to the bullets above in order.

### Verdicts by topic

**CLI surface & lifecycle** (P2, P3, P4)

- **P3 — drop `up`/`down` as the primary verbs.** *Valid, and half-done already.*
  `run` already lazily starts infra: if warden+squid aren't healthy it runs the
  preflight and `compose up -d` itself (`run.py:25-36`). So the infra-only `up` is
  already redundant for the interactive path; the only thing that genuinely needs an
  explicit start is the **remote daemon** (`up --remote` → `--profile remote`,
  `stack.py:47-96`). Keep one teardown verb (rename `down` → `stop`; `down` always
  tears the remote profile too, `stack.py:105`). Net: fold `up` away, expose start
  semantics through `run`/modes (see P2), keep `stop`.
- **P2 — modes instead of `run` vs `up --remote`.** *Good instinct, but don't build a
  plugin system for three hardcoded cases.* The real friction is that `run`
  (`compose run --rm`, ephemeral) and `up --remote` (`compose up -d`, daemon) sit at
  the same conceptual level but differ in **container lifecycle**, which a flat
  "profile" name hides. So a mode must encode lifecycle, not just an entrypoint
  subcommand. Ship `claude` / `claude-remote` / `shell` as **named built-in modes**
  (`run <mode>`), which also retires the separate `shell` subcommand
  (`run.py:59-71`). Defer "profiles as droppable assets in catraz home" until someone
  actually needs a 4th, user-defined mode — that's a plugin framework for a problem
  you don't have yet.
- **P4 — detach/reattach + list running containers of this repo.** *Valid, biggest of
  this group.* Today agent containers are throwaway (`run --rm`); only warden+squid
  persist, and `status` (`stack.py:117-140`, scoped by the per-root project hash
  `catraz-<base>-<sha8>`) is the only listing. Reattach implies a **named,
  long-running agent container** — a new lifecycle that depends on the P2 mode model.
  Listing is cheap (filter `compose ps` to agent services); detach/reattach is the
  real work.
- **P9 — remove `prune`.** *Agreed, cut it.* It's a ~5-line wrapper
  (`image.py:37-41`, `cli.py:47-51`) that runs `docker image rm` over every
  `catraz-base` image — i.e. a top-level verb for what `docker image rm catraz-base`
  (or `docker image prune`) already does. The only thing it buys is not having to know
  the image is named `catraz-base`, which a one-line doc note covers. A rarely-used
  cleanup convenience doesn't earn permanent CLI surface. Delete the command + parser;
  if you want a home for it, fold image cleanup into `doctor --fix` rather than a verb
  of its own.

**Onboarding & config** (P1, P8)

- **P1 — auto-offer GitLab repos as allowed_projects + an amend command.** *Valid and
  cheap.* There is currently **no auto-detection** and **no amend command** — projects
  are typed by hand in the wizard (`_wizard_interactive.py:81-112`) or pulled from
  `WARDEN_ALLOWED_PROJECTS`, then written into `warden.toml` via `set_toml_list`
  (`policy.py:70-91`); to change them later you re-run `init` or hand-edit the toml.
  Two small pieces: (a) scan `/workspace` (or the init folder) for git remotes whose
  host matches `GITLAB_URL`, derive the project path, and offer them in the wizard;
  (b) a `catraz allow <path>…` command that validates (`validate_project` — no
  wildcards) and appends to `warden.toml`. Mind R5/§6.10: keep it an explicit,
  enumerable list — offer, don't auto-add silently.
- **P8 — a config-refresh command.** *Valid; your assumption is correct.* Confirmed:
  the Warden loads a **frozen `Config` once at startup** (`warden/config.py`,
  `__main__._serve`), no reload path — so running containers do **not** change
  behaviour when `.catraz` changes. So this is purely CLI ergonomics: a command that
  detects stale config against running containers, warns, and restarts the affected
  services. Name it for what it does at runtime — `catraz reload` (or `restart`).
  **Don't overload `init`**: init is onboarding/scaffolding, reload is a lifecycle op;
  conflating them muddies both. `reinit` is the worst of the names — it sounds
  destructive.

**Observability** (P10)

- **P10 — persist container logs into `.catraz/logs`.** *Worth doing, but it's only the
  agent that's missing.* The infra already does this: Warden and Squid bind-mount their
  app logs into `.catraz/logs/warden` and `.catraz/logs/squid`
  (`docker-compose.yml:41,69`). The gap is the **agent** container — its stdout/stderr
  lives only in Docker's json-file log driver (read via `catraz logs agent`,
  `observe.py:50-61`), and because `run` uses `--rm`, that record **disappears when the
  one-off exits**. So a session's Claude output is gone the moment it ends. Fix: tee the
  agent's stdout/stderr to `.catraz/logs/agent/<timestamp>.log` (entrypoint redirect or
  a bind-mounted log dir), giving a durable per-run transcript next to the warden/squid
  logs. Mind two things: this dir is on the host side of the tmpfs `.catraz` shadow
  (fine — the agent can't read `.catraz`), and add rotation/retention so long sessions
  don't grow unbounded.

**Proxy & network architecture** (P5, P7)

- **P5 — hide the `insteadOf` rewrite / make warden interception transparent.**
  *Half right, half muddled.* The clean kernel: today git redirection is a
  `~/.gitconfig` `url.<warden>.insteadOf https://gitlab.com/` rewrite injected at
  container start (`entrypoint.py:116-136`), and the agent reaches the Warden
  **directly** (`no_proxy=gitlab-warden`, `docker-compose.yml:126`) while Squid is a
  **parallel** egress path. Making interception transparent (agent keeps canonical
  `gitlab.com` remotes, the network routes them to the Warden) is a legitimate goal —
  but it isn't free: transparent HTTPS interception means either DNS/`/etc/hosts`
  spoofing into a plaintext warden listener, or terminating TLS. The Warden currently
  speaks **plain HTTP** to the agent, so this is a real design change, not a cleanup.
  The **"reduce load on the Warden by going forwarder→warden sequentially"** argument
  is backwards: the Warden today already sees **only** git/API traffic (npm/pypi never
  touch it — gitlab.com is deliberately *absent* from the Squid allowlist so a tokened
  remote can't bypass the Warden). Funnelling everything through Squid first **adds** a
  hop and makes Squid a fatter chokepoint; it doesn't lighten the Warden. And the
  defense-in-depth you want is **already structural**: `agent-net` is `internal: true`
  (no egress), so if the Warden is down the agent has *no* route to gitlab.com,
  GITLAB_MODE or not. Keep the transparency goal; **drop the load/sequential
  rationale** — it's solving a non-problem.
- **P7 — replace Squid with Traefik/Caddy.** *Reject for now — this is aesthetics
  shopping.* The Squid config is **~53 lines**, stateless, and uses exactly one narrow
  feature: SNI peek+splice (no TLS interception) + a **file-based** domain allowlist
  with `squid -k reconfigure` hot-reload (`forward-proxy/`, `squid.conf`). Traefik
  (TCP/SNI routers) and Caddy (layer4 plugin) can do SNI routing but **neither has
  native file-based domain-allowlist reloading** — you'd write a plugin to replace a
  working 53-line config. "Nicer config syntax" is not a reason to re-platform the one
  component standing between the agent and the open internet. Revisit only on a
  concrete trigger (CVE, an actual maintainability wall, a needed feature). Until then:
  document the squid.conf better and move on.

**Warden extensibility** (P6)

- **P6 — adapter abstraction (GitLab/GitHub/…) in the Warden.** *Reasonable direction,
  but the abstraction is partly there and the second provider isn't.* The Warden is
  one Starlette process but **git and API paths are already route-separated**
  (`git_proxy.py` vs `api_proxy.py`, split in `app.py:21-28`) over a shared pure-policy
  core (`policy.decide`, `model.*`, `upstream.Upstream`), and writes are already
  **data-driven** via the `WriteEndpoint` table (`api_endpoints.py`). What's missing is
  a named "provider adapter" seam and an enable/disable list. The honest move: don't
  build a GitHub plugin framework speculatively (classic premature abstraction). When
  GitHub is a *concrete* requirement, extract the GitLab-specific bits (endpoint table,
  URL shapes, push-rule semantics) behind one interface so the seam is visible — then
  adding/toggling adapters falls out. Until then, mark it as a **refactor-on-demand**,
  not a now task.

### Condensed worklist (smallest → biggest, deps respected)

1. **Remove `prune`** (P9) — delete the command + parser (`image.py:37-41`,
   `cli.py:47-51`); `docker image rm catraz-base` already does it. Pure surface
   reduction, smallest possible change.
2. **`catraz reload`** (P8) — detect config changed vs running containers, warn,
   restart affected services. Confirmed-safe assumption, pure ergonomics. *Don't name
   it `init`/`reinit`.*
3. **Persist agent logs to `.catraz/logs/agent`** (P10) — tee the agent's stdout/stderr
   to a per-run file next to the existing warden/squid logs, so `--rm` sessions leave a
   transcript. Add rotation.
4. **List active agent containers** (P4) — filter `compose ps` to the agent service(s)
   for this repo. Easy. *(Detach/reattach split out — it needs the P2 persistent-mode
   lifecycle and isn't part of this step.)*
5. **Collapse `up`, rename `down`→`stop`** (P3) — `run` already lazy-starts infra; keep
   explicit start only for the remote daemon. Mostly surface reduction; precedes P2.
6. **`catraz allow` + GitLab repo auto-offer in the wizard** (P1) — append-to-`warden.toml`
   command + scan remotes against `GITLAB_URL`. Offer, never auto-add.
7. **Named run modes** `claude` / `claude-remote` / `shell` (P2) — unify `run` vs
   `up --remote`, retire the `shell` subcommand. Depends on the P3 lifecycle decision.
   *Built-in modes, not a plugin/asset system.*
8. **Warden provider-adapter seam** (P6) — *only when GitHub (or another provider) is a
   real requirement.* Extract GitLab specifics behind one interface + an active-adapter
   list. Refactor-on-demand, not speculative.
9. **Transparent Warden interception, retire `insteadOf`** (P5) — design spike first
   (DNS/hosts spoof to a plaintext listener vs TLS termination); security-sensitive,
   touches the trust boundary. *Pursue the transparency goal, drop the "reduce warden
   load / sequential forwarder" rationale.*
10. **Squid replacement evaluation** (P7) — **deferred/rejected** as a near-term task;
    keep Squid, document its config, revisit only on a concrete trigger.