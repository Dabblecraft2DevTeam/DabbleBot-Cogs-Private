## 2026-05-02 - Information Leakage in Exception Handling
**Vulnerability:** Raw `aiomysql` and configuration exception details were being exposed directly to Discord users via the `rank` command in the `nbzhc_rank.py` cog.
**Learning:** Sending `str(e)` directly to end-users from database connection or query failures can leak sensitive backend structure, configuration data, and connection strings, which an attacker could leverage to discover system internals or credentials.
**Prevention:** Always catch exceptions securely. Log the raw exception details securely server-side (e.g., console or a logging framework), and present a generic, sanitized error message to the end user.
## 2026-05-02 - Missing Authorization Decorators
**Vulnerability:** Global administration commands and channel creation commands lacked Red-DiscordBot authorization decorators (`@commands.admin_or_permissions()` or `@commands.is_owner()`), enabling any Discord user to exploit them.
**Learning:** Forgetting to add permission decorators on administration or state-mutating commands creates a critical authorization bypass, allowing unauthorized users to perform sensitive actions (like modifying global states or creating arbitrary channels).
**Prevention:** Always ensure administrative or state-mutating commands are decorated with appropriate permission checks (e.g., `@commands.admin_or_permissions(...)`, `@commands.is_owner()`) and `@commands.guild_only()` when interacting with server-specific features.
## 2026-05-02 - SSRF and Path Traversal Risk in External API Call
**Vulnerability:** The `rank` command in `NBZHCRank/nbzhc_rank.py` interpolated unvalidated user input (`playername`) directly into a Mojang API URL (`https://api.mojang.com/users/profiles/minecraft/{playername}`). This could allow an attacker to use Path Traversal characters (e.g., `../`, `%2e%2e%2f`) or unexpected URL components to manipulate the API request, leading to Server-Side Request Forgery (SSRF) or unexpected application behavior.
**Learning:** Even when interpolating user input into seemingly "safe" external API URLs, unvalidated input can alter the intended path or query parameters, creating SSRF or Path Traversal risks.
**Prevention:** Always strictly validate and sanitize user input against an allowlist pattern (e.g., regex `^[a-zA-Z0-9_]{1,16}$` for Minecraft usernames) *before* interpolating it into any URLs or external system requests.
## 2026-05-02 - Missing Embed Validation and SSRF Risk
**Vulnerability:** `CaptchaGate/captchagate.py` lacked validation for external image URLs (`image_url`) and text limits (`options_list`, `captchaset_welcometitle`, `captchaset_welcomedesc`) provided by admins. This missing embed validation can lead to Discord API 400 errors (Self-DoS) and unvalidated URLs pose an SSRF risk via Discord's media proxy.
**Learning:** Always validate external resources strictly (e.g., scheme `http://` or `https://` for image URLs) and enforce character limits before they are sent to Discord to prevent bot failures and proxy exploits.
**Prevention:** Implement strict length checking based on Discord API limits (e.g., embed titles <= 256, descriptions <= 4096, URLs <= 2048, and button labels <= 80 chars). Ensure URL inputs enforce a strict scheme allowlist.

## 2026-06-20 - Prevent Information Leakage in Logs
**Vulnerability:** Debug prints and exception logs exposed sensitive database configuration details (like host, user, db, and full stack traces).
**Learning:** Including user-provided configuration values and raw exception objects in unauthenticated standard output channels causes severe information leakage.
**Prevention:** Ensure debug prints are removed before deployment and use `type(e).__name__` for exception logging to suppress sensitive traceback data.
