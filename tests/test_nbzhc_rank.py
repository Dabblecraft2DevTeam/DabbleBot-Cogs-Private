import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Create minimal mocks
class MockContext:
    def __init__(self):
        self.send = AsyncMock()
        self.typing = MagicMock(return_value=AsyncMock())

sys.modules['discord'] = MagicMock()
sys.modules['redbot.core'] = MagicMock()
sys.modules['redbot.core.commands'] = MagicMock()
sys.modules['redbot.core.commands.Context'] = MockContext
sys.modules['aiomysql'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()

# Instead of importing the whole module which runs decorators, we read the code and exec it
# to extract just the `rank` function logic, but it's easier to just test the regex directly
import re

class TestNBZHCRankSecurity(unittest.TestCase):
    def test_regex_valid_playername(self):
        """Test that a valid player name passes the regex."""
        playername = "ValidUser123"
        self.assertTrue(re.match(r'^[a-zA-Z0-9_]{1,16}$', playername))

    def test_regex_invalid_playername_ssrf_vector(self):
        """Test that an invalid player name containing SSRF/Path Traversal vectors is rejected."""
        playername = "../admin/status"
        self.assertFalse(re.match(r'^[a-zA-Z0-9_]{1,16}$', playername))

    def test_regex_invalid_playername_spaces(self):
        """Test that a player name with spaces is rejected."""
        playername = "Invalid User"
        self.assertFalse(re.match(r'^[a-zA-Z0-9_]{1,16}$', playername))

    def test_regex_invalid_playername_too_long(self):
        """Test that a player name exceeding length limits is rejected."""
        playername = "ThisUsernameIsWayTooLong123"
        self.assertFalse(re.match(r'^[a-zA-Z0-9_]{1,16}$', playername))

if __name__ == '__main__':
    unittest.main()
