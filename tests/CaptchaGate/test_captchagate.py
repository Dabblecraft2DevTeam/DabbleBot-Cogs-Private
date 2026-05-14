import sys
from unittest.mock import MagicMock, AsyncMock
import asyncio

# Mock redbot and discord modules
discord = MagicMock()
sys.modules['discord'] = discord
sys.modules['discord.ext'] = MagicMock()

redbot = MagicMock()
redbot.core = MagicMock()
redbot.core.commands = MagicMock()

# Instead of Mocking commands.Cog.listener, we need to handle the Cog base class correctly.
# If we just mock commands.Cog it might be an uninstantiable mock.
class MockCog:
    @classmethod
    def listener(cls, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

redbot.core.commands.Cog = MockCog

# Mock commands.group and commands.command to just return the function
class MockGroup:
    def __init__(self, func):
        self.func = func

    def command(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

    def group(self, *args, **kwargs):
        def decorator(f):
            return MockGroup(f)
        return decorator

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

def mock_group(*args, **kwargs):
    def decorator(func):
        return MockGroup(func)
    return decorator

def mock_decorator(*args, **kwargs):
    def decorator(func):
        return func
    return decorator

redbot.core.commands.group = mock_group
redbot.core.commands.guild_only = mock_decorator
redbot.core.commands.admin_or_permissions = mock_decorator
redbot.core.commands.command = mock_decorator

sys.modules['redbot'] = redbot
sys.modules['redbot.core'] = redbot.core
sys.modules['redbot.core.bot'] = MagicMock()
sys.modules['redbot.core.utils.chat_formatting'] = MagicMock()
sys.modules['redbot.core.utils.menus'] = MagicMock()
sys.modules['redbot.core.commands'] = redbot.core.commands

# Now we can import the cog safely
from CaptchaGate.captchagate import CaptchaGate

async def run_tests():
    print("Starting CaptchaGate Embed Validation Tests...")

    # Mock the bot
    bot = MagicMock()

    cog = CaptchaGate(bot)

    # Mock context
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.guild = MagicMock()

    # Test 1: Title Validation
    ctx.send.reset_mock()
    long_title = "A" * 257
    await cog.captchaset_welcometitle(ctx, title=long_title)
    ctx.send.assert_called_with("❌ Title cannot exceed 256 characters.")
    print("Test 1: Title validation passed.")

    # Test 2: Description Validation
    ctx.send.reset_mock()
    long_desc = "A" * 4097
    await cog.captchaset_welcomedesc(ctx, description=long_desc)
    ctx.send.assert_called_with("❌ Description cannot exceed 4096 characters.")
    print("Test 2: Description validation passed.")

    # Test 3: Image URL Validation
    ctx.send.reset_mock()
    long_url = "A" * 2049
    await cog.challenge_add(ctx, "test_1", long_url, "Correct", options="Correct, Wrong")
    ctx.send.assert_called_with("❌ Image URL cannot exceed 2048 characters.")
    print("Test 3: Image URL validation passed.")

    # Test 4: Option Length Validation
    ctx.send.reset_mock()
    long_option = "A" * 81
    await cog.challenge_add(ctx, "test_2", "valid_url", "Correct", options=f"Correct, {long_option}")
    ctx.send.assert_called_with("❌ Each option (button label) cannot exceed 80 characters.")
    print("Test 4: Option length validation passed.")

    print("All tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
