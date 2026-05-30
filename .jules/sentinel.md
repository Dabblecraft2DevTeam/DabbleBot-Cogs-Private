## 2023-10-27 - [Discord Embed and View Limits Mitigation]
**Vulnerability:** The application was not validating the length of user-provided strings before using them in Discord Embed properties (title, description) and UI Components (button labels, image URLs).
**Learning:** The Discord API strictly enforces character limits (e.g., 256 for embed titles, 4096 for descriptions, 80 for button labels). Exceeding these limits causes unhandled `HTTPException`s (400 Bad Request), which can lead to application crashes or denial of service when processing user input.
**Prevention:** Always validate and enforce length limits on user input that maps directly to Discord API constructs before attempting to send the payload.
