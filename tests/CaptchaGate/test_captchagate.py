import unittest
from unittest.mock import AsyncMock, MagicMock
import sys

# Mock modules
sys.modules['discord'] = MagicMock()
sys.modules['discord.ext'] = MagicMock()
sys.modules['discord.ext.tasks'] = MagicMock()
sys.modules['discord.ui'] = MagicMock()
sys.modules['redbot'] = MagicMock()
sys.modules['redbot.core'] = MagicMock()
sys.modules['redbot.core.bot'] = MagicMock()
sys.modules['redbot.core.utils'] = MagicMock()
sys.modules['redbot.core.utils.chat_formatting'] = MagicMock()
sys.modules['redbot.core.utils.menus'] = MagicMock()

# Mock decorators
class MockDecorator:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, func):
        func.command = self.__class__
        func.group = self.__class__
        return func

class MockCog:
    @classmethod
    def listener(cls, *args, **kwargs):
        return MockDecorator()

sys.modules['redbot.core'].commands = MagicMock()
sys.modules['redbot.core'].commands.command = MockDecorator
sys.modules['redbot.core'].commands.group = MockDecorator
sys.modules['redbot.core'].commands.guild_only = MockDecorator
sys.modules['redbot.core'].commands.admin_or_permissions = MockDecorator
sys.modules['redbot.core'].commands.Cog = MockCog

from CaptchaGate.captchagate import CaptchaGate

class TestCaptchaGateValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.cog = CaptchaGate(self.bot)
        self.ctx = AsyncMock()
        self.ctx.send = AsyncMock()
        self.ctx.guild = MagicMock()

    async def test_captchaset_welcometitle_too_long(self):
        long_title = "a" * 257
        await self.cog.captchaset_welcometitle(self.ctx, title=long_title)
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIn("exceed 256 characters", args[0])

    async def test_captchaset_welcometitle_valid(self):
        valid_title = "a" * 256
        self.cog.config = MagicMock()
        self.cog.config.guild.return_value.welcome_embed_title.set = AsyncMock()
        await self.cog.captchaset_welcometitle(self.ctx, title=valid_title)
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertNotIn("exceed 256 characters", args[0])

    async def test_captchaset_welcomedesc_too_long(self):
        long_desc = "a" * 4097
        await self.cog.captchaset_welcomedesc(self.ctx, description=long_desc)
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIn("exceed 4096 characters", args[0])

    async def test_captchaset_welcomedesc_valid(self):
        valid_desc = "a" * 4096
        self.cog.config = MagicMock()
        self.cog.config.guild.return_value.welcome_embed_desc.set = AsyncMock()
        await self.cog.captchaset_welcomedesc(self.ctx, description=valid_desc)
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertNotIn("exceed 4096 characters", args[0])

    async def test_challenge_add_url_too_long(self):
        long_url = "a" * 2049
        await self.cog.challenge_add(self.ctx, challenge_id="test", image_url=long_url, correct_option="a", options="a, b")
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIn("exceed 2048 characters", args[0])

    async def test_challenge_add_option_too_long(self):
        valid_url = "a" * 2048
        long_option = "a" * 81
        await self.cog.challenge_add(self.ctx, challenge_id="test", image_url=valid_url, correct_option="a", options=f"a, {long_option}")
        self.ctx.send.assert_called_once()
        args, kwargs = self.ctx.send.call_args
        self.assertIn("exceed 80 characters", args[0])

    async def test_challenge_add_valid(self):
        valid_url = "a" * 2048
        valid_option = "a" * 80
        self.cog.config = MagicMock()

        # Mock the async context manager for challenges()
        mock_challenges_dict = {}
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_challenges_dict
        self.cog.config.guild.return_value.challenges.return_value = mock_cm

        await self.cog.challenge_add(self.ctx, challenge_id="test", image_url=valid_url, correct_option=valid_option, options=f"b, {valid_option}")

        # Check if success message was sent
        self.assertTrue(self.ctx.send.called)

        # The last call to ctx.send should be the success message
        args, kwargs = self.ctx.send.call_args
        self.assertIn("added", args[0])

if __name__ == "__main__":
    unittest.main()
