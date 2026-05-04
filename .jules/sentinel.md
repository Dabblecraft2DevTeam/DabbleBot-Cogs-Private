## 2026-05-02 - Information Leakage in Exception Handling
**Vulnerability:** Raw `aiomysql` and configuration exception details were being exposed directly to Discord users via the `rank` command in the `nbzhc_rank.py` cog.
**Learning:** Sending `str(e)` directly to end-users from database connection or query failures can leak sensitive backend structure, configuration data, and connection strings, which an attacker could leverage to discover system internals or credentials.
**Prevention:** Always catch exceptions securely. Log the raw exception details securely server-side (e.g., console or a logging framework), and present a generic, sanitized error message to the end user.
## 2026-05-02 - Missing Authorization Decorators
**Vulnerability:** Global administration commands and channel creation commands lacked Red-DiscordBot authorization decorators (`@commands.admin_or_permissions()` or `@commands.is_owner()`), enabling any Discord user to exploit them.
**Learning:** Forgetting to add permission decorators on administration or state-mutating commands creates a critical authorization bypass, allowing unauthorized users to perform sensitive actions (like modifying global states or creating arbitrary channels).
**Prevention:** Always ensure administrative or state-mutating commands are decorated with appropriate permission checks (e.g., `@commands.admin_or_permissions(...)`, `@commands.is_owner()`) and `@commands.guild_only()` when interacting with server-specific features.
## 2024-05-18 - SSRF and Path Traversal via External API Calls
**Vulnerability:** In `nbzhc_rank.py`, user input (`playername`) was directly interpolated into the URL path for an external Mojang API call without prior validation.
**Learning:** Failing to validate user input before constructing URLs for external HTTP requests can lead to Server-Side Request Forgery (SSRF) and Path Traversal, allowing attackers to manipulate the destination or request parameters.
**Prevention:** Always validate and sanitize user input against a strict allowlist (e.g., using regex to ensure it only contains valid characters) before using it to construct external requests or file paths.

## 2024-05-18 - Resource Exhaustion DoS via ThreadPoolExecutor Leaks
**Vulnerability:** In `customping.py`, a new `ThreadPoolExecutor` was instantiated for every command invocation to run synchronous `speedtest` functions off the main event loop, but the executor was never explicitly shut down.
**Learning:** Localized thread or process pool executors will leak resources if not properly managed or shut down. In a high-traffic environment, this leads to rapid thread exhaustion and a Denial of Service (DoS) condition.
**Prevention:** Use the event loop's default executor via `loop.run_in_executor(None, ...)` for offloading synchronous tasks to avoid instantiating unmanaged executors, or ensure explicit lifecycle management (e.g., using `with` statements) when custom executors are strictly necessary.
