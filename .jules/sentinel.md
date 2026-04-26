## 2025-02-28 - Database Credential Exposure in Configurations and Logs
**Vulnerability:** MySQL database credentials (including passwords) were explicitly logged to standard output (`print` statements) during configuration and when establishing connections. Additionally, database connection and query errors exposed internal config dictionaries (containing passwords) and raw exception details directly in Discord messages via the `ValueError` and `Exception` handling.
**Learning:** In the Red-DiscordBot framework, `config.all()` pulls the complete configuration dictionary which can contain sensitive credentials. Calling `print()` on this object or appending it to string-formatted error messages leaks the credentials. The same applies to returning generic raw `Exception` strings to the end user.
**Prevention:**
- Never log configuration states containing credentials using `print()`, standard logging, or any other output method.
- Catch exceptions securely; do not attach configuration dictionaries (`config_data`) or raw exception strings (`e`) directly to user-facing error messages in Discord.
- Ensure that internal tracebacks/errors are securely handled and either dropped or logged properly internally, without leaking passwords.
