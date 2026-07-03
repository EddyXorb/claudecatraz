# Git Smart-HTTP — Protocol Reference

Git communicates over HTTP using exactly three endpoints. Two of them are a
handshake (Ref Advertisement) and one carries out the actual operation
(Pack Transfer). This is called **Smart HTTP**.

## URL, Remote, and Repo Path

A Git remote is a URL in `.git/config`:

```text
[remote "origin"]
    url = https://warden.example.com/git/group/proj.git
```

The URL has two parts that Git separates:

- **Host** — `warden.example.com` (or `gitlab.com` for direct access).
  Git opens a TCP connection on port 443 (HTTPS) or 80 (HTTP).
- **Path** — everything after that, here `/git/group/proj.git`. This is the `{repo}`
  in all Smart-HTTP endpoints.

Git then appends the endpoint suffixes to this path:

```text
https://warden.example.com/git/group/proj.git/info/refs?service=git-receive-pack
https://warden.example.com/git/group/proj.git/git-receive-pack
```

For direct GitLab access (`https://gitlab.com/group/proj.git`) the `/git/`
prefix is omitted — GitLab mounts the git endpoint directly under the root.
The Warden needs this prefix because it exposes both git routes (`/git/…`)
and API routes (`/api/v4/…`) on port 8080.

The remote URL in `.git/config` remains **canonical** (`https://gitlab.com/…`)
— identical to a normal clone. The redirect to the Warden is stored
exclusively in the global container `~/.gitconfig` and is set once at
container start:

```text
git config --global \
  url."http://gitlab-warden:8080/git/".insteadOf "https://gitlab.com/"
```

This means the rewrite only applies inside the container (where `~/.gitconfig`
exists), not on the host (different `$HOME`). `git remote -v` shows the
rewritten address inside the container; `.git/config` and the host remain
untouched. The `/git/` prefix in the rewrite target is required for routing
on port 8080 to work.

## HTTP in Brief

HTTP is a plain-text request-response protocol over TCP (in practice almost
always as HTTPS over TLS). Each request consists of:

```text
GET /git/group/proj.git/info/refs?service=git-receive-pack HTTP/1.1
Host: warden.example.com
Authorization: Basic <base64(user:token)>
Git-Protocol: version=2         ← optional capability header
                                ← blank line = end of headers
```

The response:

```text
HTTP/1.1 200 OK
Content-Type: application/x-git-receive-pack-advertisement
                                ← blank line
<pkt-line Body>
```

For git operations HTTP uses two methods:

- `GET` — Ref Advertisement (`info/refs`), no body
- `POST` — Pack Transfer (`git-upload-pack`, `git-receive-pack`), binary body

The `Content-Type` uniquely identifies which git endpoint is being addressed
(`application/x-git-upload-pack-request` etc.). A normal HTTP proxy without
git awareness would simply forward these requests — the Warden, however,
actively parses the body.

## The Repo Path in the Warden

In all three endpoints `{repo}` represents the full path of the repository
on the server — without a leading slash, with a `.git` suffix.
In GitLab this is the namespace path: `group/projekt.git` for a
top-level repo, `group/subgroup/projekt.git` for a subgroup repo.
Any number of namespace levels are possible.

The Warden mounts the endpoints under the prefix `/git/` and captures the
repo path using the Starlette converter `:path`, which unlike `:str`
also matches slashes:

```text
GET  /git/{project:path}/info/refs
POST /git/{project:path}/git-upload-pack
POST /git/{project:path}/git-receive-pack
```

`project` in `path_params` then contains e.g. `group/subgroup/projekt.git`.
The config method `project_allowed()` removes the `.git` suffix before the
allowlist comparison.

## Endpoints

### GET `/{repo}/info/refs?service=<service>`

Ref Advertisement — the handshake. The client asks which refs (branches,
tags) the server knows about and which capabilities it supports. `service` is
`git-upload-pack` for fetch/clone or `git-receive-pack` for push.

The server responds with a pkt-line stream:

```text
001e# service=git-upload-pack\n    ← service header
0000                                ← flush (end of header)
00b4<sha1> HEAD\0side-band-64k ...  ← first ref + capabilities (NUL-separated)
003f<sha1> refs/heads/main\n
0000                                ← flush (end of ref list)
```

The Warden forwards this endpoint completely — after the allowlist check,
without further inspection. Fetch/clone uses the read token, push uses the
write token (based on `service`).

### POST `/{repo}/git-upload-pack`

Fetch / Clone. The client sends a `want` list (which commits it wants)
and a `have` list (what it already has). The server responds with a packfile
containing exactly the missing delta.

```text
0032want <sha1> side-band-64k ofs-delta\n
0000
0009done\n
```

Response with `side-band-64k` is multiplexed over three channels: `\x01` packfile data,
`\x02` progress (stderr on the client), `\x03` fatal error.

Warden forwards completely — after allowlist check with read token, no
inspection of the body.

### POST `/{repo}/git-receive-pack`

Push. The request body consists of two sections, separated by a
flush packet:

```text
Section 1 — Ref commands (parsed by the Warden):
00a9<old-sha1> <new-sha1> refs/heads/claude/x\0report-status side-band-64k\n
0000   ← flush

Section 2 — Packfile (never buffered by the Warden):
PACK....
```

The Warden reads only section 1 completely (up to the flush), polices the
ref commands, and then streams the unchanged body (section 1 + 2) onwards.
The packfile is never held entirely in memory at any point.

## pkt-line Format

Each packet begins with a 4-digit hex number indicating the total length of
the packet including the 4 length bytes themselves:

```text
0006a\n   ← length 6, content "a\n"
0000      ← flush packet (length 0) — separator between sections
```

`0001` (delimiter) and `0002` (response-end) are protocol-v2 extensions
that the Warden does not interpret.

## Complete Example: git push

```text
git push origin claude/feature
```

### Step 0 — Look up the remote URL

Git reads `.git/config`, finds `url = https://warden.example.com/git/group/proj.git`
and derives the host (`warden.example.com:443`) and the repo path (`group/proj.git`)
from it.

### Step 1 — Ref Advertisement (GET)

```text
→ GET /git/group/proj.git/info/refs?service=git-receive-pack HTTP/1.1
     Host: warden.example.com
     Authorization: Basic <credentials>

← 200 OK  Content-Type: application/x-git-receive-pack-advertisement
     001e# service=git-receive-pack\n
     0000
     00b4<sha-main> refs/heads/main\0report-status side-band-64k\n
     003c<sha-old>  refs/heads/claude/feature\n
     0000
```

The Warden checks here: is the project in the allowlist? Token injection (write token
instead of agent credentials). Then forwards to GitLab.

Git now compares the advertised refs with the local state and determines:
`claude/feature` points to `<sha-old>`, locally it is now `<sha-new>` —
so there is something to push.

### Step 2 — Push (POST)

Git packs the missing objects into a packfile and sends:

```text
→ POST /git/group/proj.git/git-receive-pack HTTP/1.1
     Content-Type: application/x-git-receive-pack-request

     00a9<sha-old> <sha-new> refs/heads/claude/feature\0report-status side-band-64k\n
     0000          ← end of section 1
     PACK<binary object data>   ← section 2
```

The Warden reads section 1 completely, parses the ref commands and calls
`decide()`:

- R6: `group/proj` in allowlist? ✓
- R2: `claude/feature` has prefix `claude/`? ✓
- R4: no delete (old ≠ 0000…) — an irreversible verb, never permitted? ✓
- R5: quotas (branches, rate, lock)? ✓

All checks pass → Warden writes an audit log entry, injects the write
token, and streams the complete body (section 1 + 2) to GitLab.

### Step 3 — Response to the Client

GitLab responds with a pkt-line stream that the Warden forwards unbuffered:

```text
← 200 OK
     0030\x01unpack ok\n           ← packfile was unpacked
     0036\x01ok refs/heads/claude/feature\n  ← ref accepted
     0000
```

The git client displays:

```text
To https://warden.example.com/git/group/proj.git
   sha-old..sha-new  claude/feature -> claude/feature
```

On a policy violation (e.g. wrong prefix) the Warden itself responds
with `403` — without forwarding the request to GitLab.

## Special Cases

**Git LFS** — objects are transferred via a separate endpoint
(`/{repo}/info/lfs/objects/batch`). This is not mounted in the Warden;
LFS requests fall through to the API proxy or receive a 404.

**Push Certificates** (`git push --signed`) — insert an additional
pkt-line section before the first flush in the receive-pack body. `parse_commands`
reads up to the flush; the certificate lands unparsed in the `head` buffer and
is forwarded unchecked.

**Git Protocol v2** — from git 2.26 onward the client can send the header
`Git-Protocol: version=2`, which activates a more efficient handshake.
The Warden does not explicitly forward this header; GitLab falls back to
protocol v1 in that case.

**SSH** — `git+ssh://` is a separate protocol and is entirely outside
the Warden's scope.
