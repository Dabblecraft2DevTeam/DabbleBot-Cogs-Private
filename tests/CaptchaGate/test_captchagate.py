import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Mock discord and redbot modules before importing the cog
discord = MagicMock()
commands = MagicMock()
redbot_core = MagicMock()
redbot_core.commands = commands
sys.modules['discord'] = discord
sys.modules['discord.ui'] = MagicMock()
sys.modules['discord.ext'] = MagicMock()
sys.modules['redbot'] = MagicMock()
sys.modules['redbot.core'] = redbot_core
sys.modules['redbot.core.bot'] = MagicMock()
sys.modules['redbot.core.utils'] = MagicMock()
sys.modules['redbot.core.utils.menus'] = MagicMock()
sys.modules['redbot.core.utils.chat_formatting'] = MagicMock()

# Define a MockDecorator class to handle nested/chained decorators
class MockDecorator:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, func):
        func.command = self.__class__
        func.group = self.__class__
        return func
    def __getattr__(self, name):
        return self.__class__

commands.command = MockDecorator
commands.group = MockDecorator
commands.guild_only = MockDecorator
commands.admin_or_permissions = MockDecorator

class DummyCog:
    @classmethod
    def listener(cls, *args, **kwargs):
        return MockDecorator()

commands.Cog = DummyCog

from CaptchaGate.captchagate import CaptchaGate

class TestCaptchaGateValidation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Create a mock bot
        self.bot = AsyncMock()
        # Create an instance of the cog
        self.cog = CaptchaGate(self.bot)

        # Mock the config object
        self.cog.config = MagicMock()
        self.mock_guild_config = MagicMock()
        self.cog.config.guild.return_value = self.mock_guild_config
        self.mock_guild_config.welcome_embed_title.set = AsyncMock()
        self.mock_guild_config.welcome_embed_desc.set = AsyncMock()

        # We need a context mock
        self.ctx = AsyncMock()
        self.ctx.guild = MagicMock()
        self.ctx.send = AsyncMock()

    async def test_captchaset_welcometitle_valid(self):
        title = "A" * 256
        await self.cog.captchaset_welcometitle(self.ctx, title=title)
        self.mock_guild_config.welcome_embed_title.set.assert_called_once_with(title)
        self.ctx.send.assert_called_with(f"✅ Public welcome message title set to: `{title}`")

    async def test_captchaset_welcometitle_invalid(self):
        title = "A" * 257
        await self.cog.captchaset_welcometitle(self.ctx, title=title)
        self.mock_guild_config.welcome_embed_title.set.assert_not_called()
        self.ctx.send.assert_called_with("❌ Title cannot exceed 256 characters.")

    async def test_captchaset_welcomedesc_valid(self):
        description = "A" * 4096
        await self.cog.captchaset_welcomedesc(self.ctx, description=description)
        self.mock_guild_config.welcome_embed_desc.set.assert_called_once_with(description)
        self.ctx.send.assert_called_with(f"✅ Public welcome message description set.")

    async def test_captchaset_welcomedesc_invalid(self):
        description = "A" * 4097
        await self.cog.captchaset_welcomedesc(self.ctx, description=description)
        self.mock_guild_config.welcome_embed_desc.set.assert_not_called()
        self.ctx.send.assert_called_with("❌ Description cannot exceed 4096 characters.")

    async def test_challenge_add_image_url_invalid(self):
        image_url = "http://example.com/" + "a" * 2030 # 2049 chars
        await self.cog.challenge_add(self.ctx, "test_id", image_url, "Correct", options="Correct, Wrong")
        self.ctx.send.assert_called_with("❌ Image URL cannot exceed 2048 characters.")

    async def test_challenge_add_options_invalid(self):
        image_url = "http://example.com/image.jpg"
        option1 = "A" * 81
        await self.cog.challenge_add(self.ctx, "test_id", image_url, "Correct", options=f"Correct, {option1}")
        self.ctx.send.assert_called_with(f"❌ Option `{option1}` exceeds 80 characters.")

if __name__ == '__main__':
    unittest.main()
