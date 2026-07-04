# 07 — Compose, git-Routing & Agent-Rendering

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §1.1, §1.2, §4.1. Lies
diese zuerst. **Hängt ab von** Schritt 01–06.

## Ziel

Die Container-/Compose-Schicht (`src/catraz/…`) so verdrahten, dass mehrere kanonische
Hosts real erreichbar sind: DNS-Aliase auf den Warden, git-`insteadOf`-Schema-Rewrite pro
Host, gerenderte REST-Basis pro Host, `no_proxy` je Host, und die neuen gruppierten
Secret-Mounts.

## Umsetzung

1. **DNS-Aliase (`assets/compose/docker-compose.yml`).** Jeden Endpoint-Host über
   `extra_hosts`/Netzwerk-Alias auf den Warden-Container zeigen lassen (heute nur der eine
   `gitlab-warden`-Name). Quelle der Host-Liste: `warden.toml`-Endpoints; die CLI, die
   compose rendert/aufruft (`src/catraz/…`, `compose.run`), leitet sie ein.
2. **git-Routing pro Host (`assets/container/git_routing.py`).** `configure_git_warden`
   von „einen `GITLAB_URL` auf `gitlab-warden` umschreiben" auf **pro Host ein
   Schema-Rewrite** heben: `https://<host>/ → http://<host>:8080/` (Hostname bleibt, §1.1),
   für jeden Endpoint-Host. Den `GITLAB_MODE=off`-Sonderzweig entfernen (off = keine
   Endpoints).
3. **REST-Basis pro Host (`assets/agents/claude/adapter.py::render_instructions`).**
   `__FORGE_REST_BASE__` von *einer* Basis auf die generische Per-Host-Regel heben (§1.2):
   „für Host X → `http://X:8080/api/v4`". Der Warden-Name darf nicht in den Instruktionen
   auftauchen.
4. **`no_proxy` (`docker-compose.yml`).** Jeden gerouteten Host zu `no_proxy`/`NO_PROXY`
   hinzufügen (heute nur `gitlab-warden`), sonst landen REST-Calls im Forward-Proxy statt
   direkt beim Warden (§1.2).
5. **Secret-Mounts (`docker-compose.yml`).** Die zwei `secrets:`-Blöcke auf
   `read_tokens`/`write_tokens` umstellen und als `READ_TOKENS_FILE`/`WRITE_TOKENS_FILE`
   reichen. Die alten `gitlab_read_token`/`gitlab_write_token`-Mounts und die
   Policy-Env-Overrides (`WARDEN_ALLOWED_PROJECTS` etc.) sowie `GITLAB_MODE`/`GITLAB_URL`
   aus dem compose-`environment` entfernen (Gegenstück zu Schritt 05).
6. **`.env`-/`warden.toml`-Assets.** `assets/config/warden.toml` auf `[git.rules]` +
   `[[git.endpoint]]` umstellen; aus dem `.env`-Template die entfernten Policy-Vars
   streichen (nur Infra-Knöpfe bleiben, §3.5).

## Nicht tun

- **Kein** `insteadOf`-Pfad-Präfix-Trick — Schema-Rewrite, Hostname bleibt kanonisch (§7
  im Hauptdokument).
- Den Warden-Hostnamen **nicht** in Remotes oder gerenderte Instruktionen leaken.
- Keinen zweiten Warden-Container pro Host.

## Tests

`tests/cli/`: `git_routing` erzeugt pro Host einen korrekten Schema-Rewrite (kein
Pfad-Trick, Hostname erhalten); `render_instructions` schreibt die Per-Host-REST-Regel ohne
Warden-Namen; compose-Rendering enthält alle Endpoint-Hosts in DNS-Alias **und** `no_proxy`.
(Echte Zwei-Host-Erreichbarkeit prüft Schritt 08.)

## Verifikation

`uv run --with pytest python -m pytest tests/cli/ tests/container/ -q && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(compose): multi-host DNS aliases + git routing + rendering
```

## Fertig-Kriterium

DNS-Alias, `insteadOf`-Schema-Rewrite, gerenderte REST-Basis und `no_proxy` decken alle
Endpoint-Hosts ab; die Secret-Mounts sind `read_tokens`/`write_tokens`; alte
Policy-Env/`GITLAB_*` aus compose/`.env` entfernt; `warden.toml`-Asset im neuen Schema;
Tests grün.
