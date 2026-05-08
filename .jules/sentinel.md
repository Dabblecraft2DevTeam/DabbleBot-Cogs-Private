## 2026-05-02 - Information Leakage in Exception Handling
**Vulnerability:** Raw `aiomysql` and configuration exception details were being exposed directly to Discord users via the `rank` command in the `nbzhc_rank.py` cog.
**Learning:** Sending `str(e)` directly to end-users from database connection or query failures can leak sensitive backend structure, configuration data, and connection strings, which an attacker could leverage to discover system internals or credentials.
**Prevention:** Always catch exceptions securely. Log the raw exception details securely server-side (e.g., console or a logging framework), and present a generic, sanitized error message to the end user.
## 2026-05-02 - Missing Authorization Decorators
**Vulnerability:** Global administration commands and channel creation commands lacked Red-DiscordBot authorization decorators (`@commands.admin_or_permissions()` or `@commands.is_owner()`), enabling any Discord user to exploit them.
**Learning:** Forgetting to add permission decorators on administration or state-mutating commands creates a critical authorization bypass, allowing unauthorized users to perform sensitive actions (like modifying global states or creating arbitrary channels).
**Prevention:** Always ensure administrative or state-mutating commands are decorated with appropriate permission checks (e.g., `@commands.admin_or_permissions(...)`, `@commands.is_owner()`) and `@commands.guild_only()` when interacting with server-specific features.

## 2024-05-24 - [CRITICAL] Prevent SSRF and Path Traversal via User Input Interpolation
**Vulnerability:** User input (`playername`) was interpolated directly into an external API URL (`https://api.mojang.com/users/profiles/minecraft/{playername}`) in `NBZHCRank/nbzhc_rank.py` without any validation. This exposed the application to Server-Side Request Forgery (SSRF) and Path Traversal attacks.
**Learning:** Never trust user input, especially when used to construct external URLs or paths. Interpolating arbitrary strings can lead to malicious queries or traversing to unintended internal or external endpoints.
**Prevention:** Always validate user input against a strict allowlist or regular expression. In this case, `re.match(r'^[a-zA-Z0-9_]{1,16}$', playername)` ensures only valid Minecraft usernames are processed.
