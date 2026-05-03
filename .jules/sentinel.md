## 2026-05-02 - Information Leakage in Exception Handling
**Vulnerability:** Raw `aiomysql` and configuration exception details were being exposed directly to Discord users via the `rank` command in the `nbzhc_rank.py` cog.
**Learning:** Sending `str(e)` directly to end-users from database connection or query failures can leak sensitive backend structure, configuration data, and connection strings, which an attacker could leverage to discover system internals or credentials.
**Prevention:** Always catch exceptions securely. Log the raw exception details securely server-side (e.g., console or a logging framework), and present a generic, sanitized error message to the end user.
## 2026-05-02 - Missing Authorization Decorators
**Vulnerability:** Global administration commands and channel creation commands lacked Red-DiscordBot authorization decorators (`@commands.admin_or_permissions()` or `@commands.is_owner()`), enabling any Discord user to exploit them.
**Learning:** Forgetting to add permission decorators on administration or state-mutating commands creates a critical authorization bypass, allowing unauthorized users to perform sensitive actions (like modifying global states or creating arbitrary channels).
**Prevention:** Always ensure administrative or state-mutating commands are decorated with appropriate permission checks (e.g., `@commands.admin_or_permissions(...)`, `@commands.is_owner()`) and `@commands.guild_only()` when interacting with server-specific features.
