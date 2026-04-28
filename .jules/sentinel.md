## 2024-04-28 - Information Leakage in Red-DiscordBot Config Output
**Vulnerability:** Debug prints and unhandled exception tracebacks returned to Discord users were exposing the database credentials stored in the bot's Config system.
**Learning:** Red-DiscordBot's `config.all()` contains full configuration state, including secrets like database passwords. Printing this or returning raw exception details can leak these secrets.
**Prevention:** Never print `config.all()` or return raw exception details to Discord users. Use generic error messages and ensure logs only record safe information.
