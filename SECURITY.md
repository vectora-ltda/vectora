# Security Policy

Vectora is a self-hosted application. The attack surface is intentionally small — no remote servers, no user authentication, no exposed REST APIs. Even so, code that executes on behalf of the user needs real protections.

---

## Built-in Protections

### Terminal Execution (`terminal` tool)

The terminal is the highest-risk tool. Applied protections:

- **Command blacklist**: Blocked by default — `rm -rf`, `mkfs`, `dd if=/dev/zero`, `:(){:|:&};:` (fork bomb), and similar destructive commands. The blacklist lives in `backend/services/security.py`.
- **Timeout**: 30 seconds per execution. Processes that exceed the limit are terminated via `proc.kill()`.
- **Async execution**: Uses `asyncio.create_subprocess_shell` — never blocks the event loop or the UI.
- **No persistent shell**: Each execution is a new child process. There is no persistent terminal session between calls.

### File Operations

- **Path traversal prevention**: All paths are validated before read/write. `../../../etc/passwd` is rejected.
- **No symlink attacks**: Path validation resolves symlinks before operating.
- **Allowed directories**: `file_read`, `file_edit`, and `file_write` operate only on permitted directories (configurable via `ENABLE_FILE_OPERATIONS`).

### Regex — Anti-ReDoS Protection

The `grep` tool accepts regex patterns from the LLM. Malicious patterns can cause catastrophic backtracking (ReDoS). Protections:

- Pattern validation in `backend/services/security.py` (`is_safe_regex_pattern`)
- 20s timeout on grep execution
- File type filters (ignores `.pyc`, binaries)

### Secrets and Logs

- **No secrets in logs**: API keys are masked before any log statement.
- **`.env` never committed**: `.gitignore` includes `.env`, `.env.*` (except `.env.example`).
- **Native telemetry, opt-out**: Structured logging of turn start/end, tool calls, and unhandled errors is enabled by default (`TELEMETRY_ENABLED=true`) and stays local — events go to `~/.vectora/logs/backend.jsonl` or a dedicated file (`TELEMETRY_OUTPUT_PATH`), never to a third-party service.

### API Rate Limiting

- **Cohere rate limiter**: The background embedding worker uses a token bucket (`CohereRateLimiter`) to stay within Cohere's API limits. Configurable via `cohere_calls_per_minute` (default: 90 calls/min — 10% below the trial limit of 100/min).

### MCP Server

- **stdio mode**: Communication via stdin/stdout. No network port opened locally.
- **SSE mode**: Listens on `MCP_HOST:MCP_PORT` (default `0.0.0.0:8000`). In production, place behind a reverse proxy with TLS.
- **stderr for feedback**: The Rich startup panel goes to stderr — never pollutes the JSON-RPC channel of the MCP protocol.

---

## What Is NOT in the MVP

By design — Vectora is self-hosted, single-user:

- ❌ **Authentication**: No users, no access tokens, no OAuth.
- ❌ **TLS/SSL**: Local communication via stdio; SSE on LAN without TLS is acceptable in the MVP.
- ❌ **User-level rate limiting**: No multiple users competing for resources (API rate limiting for Cohere is implemented).
- ❌ **Full audit log**: Structured JSON logs only.
- ❌ **LLM sandboxing**: The LLM can request execution of any enabled tool.

These items are part of the roadmap for when Vectora becomes multi-tenant.

---

## Reporting a Vulnerability

If you find a security vulnerability, **do not open a public issue**. This would expose the problem before a fix is available.

**How to report:**

1. Open a [GitHub Security Advisory](https://github.com/vectora-ltda/vectora/security/advisories/new) (private)
2. Include: affected component, vulnerability type, steps to reproduce, estimated impact

**What to include:**

- Vectora version (`vectora --version`)
- Operating system
- Minimal steps to reproduce
- Expected vs. observed behavior

**What NOT to include:**

- Real secrets, tokens, or credentials
- Full technical exploit before coordinating disclosure

**Disclosure process:**

1. Maintainer acknowledges receipt within 48h
2. Validates and reproduces the vulnerability
3. Develops and tests the fix
4. Publishes fix + coordinated advisory

---

## Secure Development Practices

All submitted code must follow:

- **Validate inputs**: Never trust data coming from the LLM or from `function_results`
- **Secrets out of git**: Check `.gitignore` before any commit
- **Least-privilege**: Tools operate within the minimum necessary scope
- **Reviewed dependencies**: `uv audit` to check known advisories
- **Special attention to**: `security.py`, any code that accepts paths from the user, any subprocess execution
