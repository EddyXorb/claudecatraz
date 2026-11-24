# `secrets/` — credentials for this sandbox

Every credential catraz uses lives here, one purpose per file. The directory is
mode 0700, each file 0600, and the whole folder is gitignored — never commit it.
The agent container never sees the git tokens; only the warden does.

| File                | Holds                                                             | Read by | Takes effect         |
| ------------------- | ----------------------------------------------------------------- | ------- | -------------------- |
| `read_tokens`       | one read-scoped token per git host                                | warden  | live, within seconds |
| `write_tokens`      | one write-scoped token per git host                               | warden  | live, within seconds |
| `anthropic_api_key` | Anthropic API key (`AUTH_MODE=api_key` only)                      | agent   | next `catraz run`    |
| `claude/`           | Claude login seed copied from the host (`AUTH_MODE=subscription`) | agent   | next `catraz run`    |

## Git tokens: `read_tokens` and `write_tokens`

Both files share one format — one line per host, the host exactly as in the
`host = "…"` of its `[[git.endpoint]]` in `config/warden.toml`:

```text
# <host> <token>
gitlab.com          glpat-xxxxxxxxxxxxxxxxxxxx
gitlab.example.com  glpat-yyyyyyyyyyyyyyyyyyyy
github.com          github_pat_zzzzzzzzzzzzzzzz
```

- Blank lines and `#` comments are ignored; host matching ignores case.
- A line without both host and token, or a host listed twice in one file,
  makes the warden reject the whole file and deny all git traffic until fixed.
- A host's access is derived from which tokens exist for it:
  read + write token → read-write; read token only → read-only; no read token
  → closed (a write token alone still runs closed).
- A token line without a matching `[[git.endpoint]]` does nothing. Adding a
  new host means: a `[[git.endpoint]]` in `warden.toml`, its token lines here,
  then `catraz reload` (new hosts need new network aliases).

`catraz init` writes these lines for the host it sets up; re-run it (or `catraz init --force`
to re-enter a token) instead of editing by hand if you prefer.

### GitLab (gitlab.com or self-hosted, `type = "gitlab"`)

Use a project or personal access token of a dedicated service account.

- **Read token:** scopes `read_api`, `read_repository`. Never `api` — that
  would make the read token able to write.
- **Write token:** scope `api`. It must also be able to read its own user
  (`GET /user`); the warden uses that to tell the agent's own MRs apart.
  Classic `api`-scope tokens can; fine-grained tokens need "User: Read".

### GitHub (github.com, `*.ghe.com`, GitHub Enterprise Server, `type = "github"`)

Use fine-grained personal access tokens restricted to exactly the repositories
in `allowed_projects`.

- **Read token:** Metadata, Contents, Pull requests, Issues, Actions — all
  read-only.
- **Write token:** Contents, Pull requests and Actions read & write; Issues
  read & write only if you enabled the issue actions. Never grant the
  Workflows permission: the agent must not be able to change workflow files.
- Workflows run code from the agent's branches with whatever secrets the
  repository exposes to them. Keep those secrets behind protected
  environments and set the default `GITHUB_TOKEN` permissions to read-only.

### Applying and checking a change

Edits are picked up within a few seconds; `catraz status` shows whether the
warden runs the file you saved, rejected it (with the reason), or cannot see
it. Some editors save by replacing the file; the warden then keeps the old
content and `catraz status` reports it out of sync — run `catraz reload`.
`catraz doctor` probes each GitLab token online and flags missing or too-broad
scopes.

To rotate a token, replace its line; to revoke access to a host, delete it.

## Anthropic: `anthropic_api_key`

Only used with `AUTH_MODE=api_key` in `.catraz/.env`. The file holds the bare
key on one line, nothing else.

## Claude login: `claude/`

Only used with `AUTH_MODE=subscription`. `catraz sync` copies the host's
Claude login here; re-run it after logging in again on the host instead of
editing these files.

## Permissions

If you create a file by hand, restrict it:

```sh
chmod 700 .catraz/secrets
chmod 600 .catraz/secrets/*
```
