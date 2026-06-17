import base64
import unittest
from unittest.mock import patch, MagicMock


class StatsigIdTests(unittest.TestCase):
    """Test dynamic statsig ID generation without polluting sys.modules."""

    def test_dynamic_statsig_uses_x1_prefix(self):
        """Dynamic statsig should produce base64-encoded x1:TypeError:... string."""
        # Import the real module - no sys.modules hacking needed
        from app.dataplane.proxy.adapters.headers import _statsig_id, get_config

        dummy_config = MagicMock()
        dummy_config.get_bool.return_value = True

        with patch(
            "app.dataplane.proxy.adapters.headers.get_config", return_value=dummy_config
        ):
            with patch(
                "app.dataplane.proxy.adapters.headers.random.choice", return_value=True
            ):
                value = _statsig_id()

        decoded = base64.b64decode(value).decode()
        self.assertTrue(decoded.startswith("x1:TypeError:"))

    def test_static_statsig_returns_fixed_string(self):
        """Static statsig (dynamic_statsig=False) should return fixed base64 string."""
        from app.dataplane.proxy.adapters.headers import _statsig_id, get_config

        dummy_config = MagicMock()
        dummy_config.get_bool.return_value = False

        with patch(
            "app.dataplane.proxy.adapters.headers.get_config", return_value=dummy_config
        ):
            value = _statsig_id()

        # Should be a valid base64 string
        decoded = base64.b64decode(value).decode()
        self.assertIn("TypeError", decoded)


if __name__ == "__main__":
    unittest.main()
