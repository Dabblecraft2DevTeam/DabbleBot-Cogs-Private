## 2024-05-14 - [Missing Embed Validation in CaptchaGate]
**Vulnerability:** User input for embed descriptions and titles, as well as option button labels and image URLs, were not being validated against Discord API limitations.
**Learning:** This results in 400 Bad Request responses (Self-DoS) because Discord strictly enforces these character limits.
**Prevention:** Make sure inputs for embeds are validated explicitly.
