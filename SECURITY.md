# Security notes for songvid / Cuesheet

Reviewed 2026-10-02. Cuesheet is a single-user tool with no login, so the rule today is simple: it only
answers to this machine. Everything below is written with the later cloud move in mind.

## Threat model today (localhost)

The attacker isn't someone on the internet hitting the server directly (it listens on 127.0.0.1 only).
It's a web page you happen to visit while Cuesheet is running, using your browser as a proxy:

- **CSRF**: another site's page posts a form or `fetch` to `http://127.0.0.1:8765/api/...`.
- **DNS rebinding**: a domain that first resolves to the attacker, then to 127.0.0.1, so its page becomes
  "same origin" with Cuesheet and can read and call everything, including Animate (spends fal.ai credit).
- **Clickjacking**: Cuesheet framed invisibly inside another page.
- **Prompt injection into Claude**: text Claude reads (lyrics, imported timing files, storyboard notes) that
  tries to make it do something else, such as spend money through the chat panel's tools.
- **Claude-written scene code**: JavaScript that runs in a headless browser on your machine.

## What is in place

| Area | Protection |
| --- | --- |
| Network exposure | Server binds 127.0.0.1; `serve()` refuses any other host until real auth exists. |
| DNS rebinding | Every request must carry a localhost `Host` header, otherwise 403. |
| CSRF | Non-GET requests with a foreign `Origin`, or `Sec-Fetch-Site: cross-site`, get 403. |
| Clickjacking / XSS | `X-Frame-Options: DENY`, CSP with `frame-ancestors 'none'`, `script-src 'self'`. The front end builds DOM with `textContent`, never `innerHTML` with data. |
| Project files | `/files/...` is confined to that project (`is_relative_to`, not a string prefix) and served with `CSP: sandbox`, so an uploaded HTML file can't run as a page. |
| Path traversal | Project slugs match `^[a-z0-9][a-z0-9-]{0,60}$`; shot ids match `^[A-Za-z0-9_-]{1,40}$` and ids from Claude are replaced with `s01`, `s02`... because ids become file names. |
| Uploads | Audio capped at 300 MB, timing files at 5 MB; audio extensions allow-listed. |
| API keys | `.env` is gitignored and chmod 600. Values with spaces or line breaks are refused (no smuggling extra lines like `ANTHROPIC_BASE_URL=`). `.env` only loads the three known key names. The UI shows only the last 4 characters. |
| Claude scene code | Runs in headless Chromium with its sandbox on. Playwright aborts every request except the runtime page, and the page's CSP forbids fetch, WebSocket, images and frames. Tested: a scene that tries all of these reaches nothing. |
| Spending via chat | The Claude panel can't start Animate (that needs an explicit confirm in the UI) and can only queue Claude drawing or AI stills for up to 3 shots; bigger batches are left to the buttons. |
| Dependencies | `pip-audit` clean on 2026-10-02 apart from a setuptools advisory (build-time only). Run `pip install pip-audit && pip-audit` now and then. |
| Code execution | No `shell=True`, `eval` or pickle; YAML uses `safe_load`; ffmpeg gets argument lists. |

## OWASP Top 10 (2021), how much each matters here

- **A01 Broken access control**: the big one once it is shared. Today it's "localhost only" plus the Host and
  Origin checks. In the cloud it needs real accounts and per-project ownership checks on every route.
- **A02 Cryptographic failures**: keys at rest in `.env`. In the cloud use a secrets manager, never the repo
  or the image, and HTTPS only.
- **A03 Injection**: handled (no shell, no SQL, parameterised subprocess, pydantic validation). The modern
  version is prompt injection, covered below.
- **A04 Insecure design**: the cost controls are design decisions: confirm before paid steps, chat limited
  to cheap ones. Cloud needs per-user spend caps.
- **A05 Security misconfiguration**: docs/OpenAPI pages are off, security headers are on. In the cloud: no
  debug mode, least-privilege service account, private storage bucket.
- **A06 Vulnerable components**: unpinned dependencies; pin with a lock file before deploying and audit in CI.
- **A07 Identification and authentication**: none yet, by design. Needed before sharing (see below).
- **A08 Software and data integrity**: Claude-written JS is untrusted code and is sandboxed. Fine for one
  user; for many, run the drawing browser in its own container with no credentials.
- **A09 Logging and monitoring**: History and job cards record who did what and what it cost. The cloud
  version should log spending per user and alert on spikes.
- **A10 SSRF**: the server only downloads URLs that fal.ai or the Suno provider hand back. Before the cloud,
  restrict those downloads to the providers' domains.

OWASP's LLM Top 10 is the more relevant list for the Claude parts: prompt injection (LLM01) and excessive
agency (LLM06) are covered by the chat tool limits; unbounded consumption (LLM10) by the confirms and
max_tokens caps.

## Before it goes to the cloud

1. Real authentication (an identity provider or at least a strong per-user login) and HTTPS.
2. Per-user projects and ownership checks; no shared `projects/` folder.
3. Server-side API keys per user or per deployment, in a secrets manager, never shown in full.
4. Spend limits per user per day for Claude and fal.ai, enforced on the server.
5. Rate limits on job submission and chat.
6. Drawing browser and ffmpeg in a separate worker container with no secrets and no network.
7. Pinned dependencies, `pip-audit` in CI, and object storage instead of local disk.
8. Replace the localhost Host check with an allow-list of your real domain.
