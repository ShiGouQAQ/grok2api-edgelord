"""Edge case tests for headers.py module.

Tests cover:
- _statsig_id(): dynamic/static mode, base64 encoding, randomness
- _major_version(): browser/UA parsing edge cases
- _platform(): platform detection from UA strings
- _arch(): architecture detection from UA strings
- _client_hints(): chromium client-hints generation
- _sanitize(): unicode normalisation, whitespace, latin-1 safety
- build_http_headers(): full HTTP header construction
- build_console_headers(): console.x.ai header construction
"""

import base64
import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module loader with stubs (same pattern as test_statsig_id.py)
# ---------------------------------------------------------------------------


def _load_headers_module():
    """Load headers.py with stubbed app.* dependencies."""
    logger_stub = types.SimpleNamespace(debug=lambda *args, **kwargs: None)

    _replaced_keys = [
        "app.platform.logging.logger",
        "app.platform.config.snapshot",
        "app.platform.config.browser",
        "app.control.proxy.models",
        "app.control.proxy.config",
        "app.dataplane.proxy.adapters.profile",
    ]
    _saved = {k: sys.modules.get(k) for k in _replaced_keys}

    # Ensure parent packages exist
    for pkg in (
        "app",
        "app.platform",
        "app.platform.logging",
        "app.platform.config",
        "app.control",
        "app.control.proxy",
        "app.dataplane",
        "app.dataplane.proxy",
        "app.dataplane.proxy.adapters",
    ):
        sys.modules.setdefault(pkg, types.ModuleType(pkg))

    # Stub logger
    sys.modules["app.platform.logging.logger"] = types.SimpleNamespace(
        logger=logger_stub,
    )

    # Stub config snapshot (get_config)
    sys.modules["app.platform.config.snapshot"] = types.SimpleNamespace(
        get_config=lambda: None,
    )

    # Stub browser config constants
    sys.modules["app.platform.config.browser"] = types.SimpleNamespace(
        BROWSER_SEC_CH_UA='"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        BROWSER_SEC_CH_UA_MOBILE="?0",
        BROWSER_SEC_CH_UA_PLATFORM='"Windows"',
    )

    # Stub ProxyLease model
    sys.modules["app.control.proxy.models"] = types.SimpleNamespace(
        ProxyLease=object,
    )

    # Stub proxy config resolver
    _default_profile = types.SimpleNamespace(
        cf_cookies="",
        user_agent="",
        cf_clearance="",
        browser="chrome120",
    )
    sys.modules["app.control.proxy.config"] = types.SimpleNamespace(
        resolve_clearance_config=lambda: _default_profile,
    )

    # Stub profile module — inline dataclass to avoid importing the real module
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _StubProxyProfile:
        cf_cookies: str = ""
        user_agent: str = ""
        cf_clearance: str = ""
        browser: str = ""

    sys.modules["app.dataplane.proxy.adapters.profile"] = types.SimpleNamespace(
        ProxyProfile=_StubProxyProfile,
        resolve_proxy_profile=lambda lease: _StubProxyProfile(),
    )

    # Load the actual module
    file_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app/dataplane/proxy/adapters/headers.py"
    )
    spec = importlib.util.spec_from_file_location("test_headers_module", file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    # Restore original modules so other tests are not affected
    for k in _replaced_keys:
        if _saved[k] is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = _saved[k]

    return module


headers = _load_headers_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DynamicConfig:
    """Config that returns True for features.dynamic_statsig."""

    def get_bool(self, key, default=False):
        if key == "features.dynamic_statsig":
            return True
        return default


class _StaticConfig:
    """Config that returns False for features.dynamic_statsig."""

    def get_bool(self, key, default=False):
        return default


# Well-known user-agent strings
CHROME_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CHROME_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CHROME_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)
FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
)
EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)
SAFARI_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)
IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)
LINUX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"


# ===========================================================================
# _statsig_id
# ===========================================================================


class TestStatsigId:
    """Tests for _statsig_id() function."""

    def test_returns_string(self):
        """_statsig_id should always return a string."""
        with patch.object(headers, "get_config", return_value=_StaticConfig()):
            result = headers._statsig_id()
        assert isinstance(result, str)

    def test_static_mode_returns_fixed_value(self):
        """Static mode returns the same value every time."""
        with patch.object(headers, "get_config", return_value=_StaticConfig()):
            first = headers._statsig_id()
            second = headers._statsig_id()
        assert first == second

    def test_static_mode_decodes_to_expected_prefix(self):
        """Static mode result decodes to the fixed error message."""
        with patch.object(headers, "get_config", return_value=_StaticConfig()):
            encoded = headers._statsig_id()
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert decoded.startswith("e:TypeError:")

    def test_static_mode_is_valid_base64(self):
        """Static result must be valid base64."""
        with patch.object(headers, "get_config", return_value=_StaticConfig()):
            encoded = headers._statsig_id()
        # Should not raise
        raw = base64.b64decode(encoded)
        assert len(raw) > 0

    def test_dynamic_mode_returns_valid_base64(self):
        """Dynamic mode result must be valid base64."""
        with patch.object(headers, "get_config", return_value=_DynamicConfig()):
            encoded = headers._statsig_id()
        raw = base64.b64decode(encoded)
        assert len(raw) > 0

    def test_dynamic_mode_decodes_to_x1_prefix(self):
        """Dynamic mode result always starts with 'x1:' after decoding."""
        with patch.object(headers, "get_config", return_value=_DynamicConfig()):
            for _ in range(20):
                encoded = headers._statsig_id()
                decoded = base64.b64decode(encoded).decode("utf-8")
                assert decoded.startswith("x1:"), f"Got: {decoded!r}"

    def test_dynamic_mode_contains_typeerror(self):
        """Dynamic mode message always contains 'TypeError:'."""
        with patch.object(headers, "get_config", return_value=_DynamicConfig()):
            for _ in range(20):
                decoded = base64.b64decode(headers._statsig_id()).decode("utf-8")
                assert "TypeError:" in decoded

    def test_dynamic_mode_randomness(self):
        """Multiple dynamic calls should produce different values."""
        with patch.object(headers, "get_config", return_value=_DynamicConfig()):
            results = {headers._statsig_id() for _ in range(20)}
        # With random generation, we should get more than 1 unique value
        assert len(results) > 1

    def test_dynamic_mode_choice_true_branch(self):
        """Force random.choice(True) → 'children[...]' branch."""
        with (
            patch.object(headers, "get_config", return_value=_DynamicConfig()),
            patch.object(headers.random, "choice", return_value=True),
            patch.object(headers.random, "choices", return_value=list("abcde")),
        ):
            encoded = headers._statsig_id()
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert "children[" in decoded

    def test_dynamic_mode_choice_false_branch(self):
        """Force random.choice(False) → 'reading ...' branch (no children)."""
        with (
            patch.object(headers, "get_config", return_value=_DynamicConfig()),
            patch.object(headers.random, "choice", return_value=False),
            patch.object(headers.random, "choices", return_value=list("abcdefghij")),
        ):
            encoded = headers._statsig_id()
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert "children[" not in decoded
        assert decoded.startswith("x1:TypeError: Cannot read properties of undefined")

    def test_dynamic_mode_no_randomness_when_patched(self):
        """With patched randomness, dynamic mode should be deterministic."""
        with (
            patch.object(headers, "get_config", return_value=_DynamicConfig()),
            patch.object(headers.random, "choice", return_value=True),
            patch.object(headers.random, "choices", return_value=list("abcde")),
        ):
            first = headers._statsig_id()
            second = headers._statsig_id()
        assert first == second


# ===========================================================================
# _major_version
# ===========================================================================


class TestMajorVersion:
    """Tests for _major_version() function."""

    def test_browser_param_with_digits(self):
        """Browser param with digits → extracted directly."""
        result = headers._major_version("chrome136", CHROME_WIN_UA)
        assert result == "136"

    def test_browser_no_digits_falls_through_to_ua(self):
        """When browser has no digits, regex scans the UA string."""
        result = headers._major_version("chrome", CHROME_WIN_UA)
        # First 2-3 digit match in UA is "10" from "NT 10.0"
        assert result == "10"

    def test_chrome_ua_finds_first_digit_group(self):
        """Chrome UA → first 2-3 digit match is '10' from NT 10.0."""
        result = headers._major_version("chrome", CHROME_WIN_UA)
        assert result == "10"

    def test_firefox_ua_finds_first_digit_group(self):
        """Firefox UA → first 2-3 digit match is '10' from NT 10.0."""
        result = headers._major_version("firefox", FIREFOX_UA)
        assert result == "10"

    def test_edge_ua_finds_first_digit_group(self):
        """Edge UA → first 2-3 digit match is '10' from NT 10.0."""
        result = headers._major_version("edge", EDGE_UA)
        assert result == "10"

    def test_safari_ua(self):
        """Safari UA → first digit group is extracted."""
        result = headers._major_version("safari", SAFARI_MAC_UA)
        assert result is not None
        assert result.isdigit()

    def test_none_browser_falls_through_to_ua(self):
        """None browser → scans UA directly."""
        result = headers._major_version(None, CHROME_WIN_UA)
        assert result == "10"

    def test_none_ua_no_browser_digits(self):
        """None UA, browser without digits → None."""
        result = headers._major_version("firefox", None)
        assert result is None

    def test_both_none(self):
        assert headers._major_version(None, None) is None

    def test_empty_strings(self):
        assert headers._major_version("", "") is None

    def test_empty_browser_ua_has_digits(self):
        """Empty browser, valid UA → extracts from UA."""
        result = headers._major_version("", CHROME_WIN_UA)
        assert result == "10"

    def test_no_digit_groups(self):
        """UA without any 2-3 digit groups → None."""
        result = headers._major_version("chrome", "Mozilla/5.0 generic")
        assert result is None

    def test_single_digit_not_matched(self):
        """Single-digit numbers do NOT match (regex needs 2-3 digits)."""
        result = headers._major_version("chrome", "Version/5 Safari/5")
        assert result is None

    def test_three_digit_version(self):
        """Three-digit version matched."""
        result = headers._major_version(None, "Chrome/999.0.0.0")
        assert result == "999"

    def test_four_digit_matches_first_three(self):
        """Four-digit number: regex matches first 3 digits (greedy {2,3})."""
        result = headers._major_version(None, "Build/12345")
        assert result == "123"

    def test_iphone_ua(self):
        """iPhone UA has OS version digits."""
        result = headers._major_version("safari", IPHONE_UA)
        assert result is not None


# ===========================================================================
# _platform
# ===========================================================================


class TestPlatform:
    """Tests for _platform() function."""

    def test_windows(self):
        assert headers._platform(CHROME_WIN_UA) == "Windows"

    def test_windows_case_insensitive(self):
        """Platform detection should be case-insensitive."""
        ua = "Mozilla/5.0 (WINDOWS NT 10.0)"
        assert headers._platform(ua) == "Windows"

    def test_macos_via_mac_os_x(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        assert headers._platform(ua) == "macOS"

    def test_macos_via_macintosh(self):
        """'macintosh' alone triggers macOS."""
        ua = "Mozilla/5.0 (Macintosh; PPC Mac OS)"
        assert headers._platform(ua) == "macOS"

    def test_android(self):
        assert headers._platform(CHROME_ANDROID_UA) == "Android"

    def test_android_case_insensitive(self):
        ua = "Mozilla/5.0 (linux; android 13)"
        assert headers._platform(ua) == "Android"

    def test_ios_iphone(self):
        """iPhone UA contains 'like Mac OS X' → matched as macOS (checked first)."""
        assert headers._platform(IPHONE_UA) == "macOS"

    def test_ios_ipad(self):
        """iPad UA contains 'like Mac OS X' → matched as macOS (checked first)."""
        assert headers._platform(IPAD_UA) == "macOS"

    def test_iphone_without_mac_os_x(self):
        """iPhone UA without 'Mac OS X' → matched as iOS."""
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS) Mobile"
        assert headers._platform(ua) == "iOS"

    def test_linux(self):
        """Linux UA → 'Linux' (per actual implementation)."""
        assert headers._platform(LINUX_UA) == "Linux"

    def test_linux_chrome(self):
        """Chrome on Linux returns Linux."""
        ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0"
        assert headers._platform(ua) == "Linux"

    def test_empty_string(self):
        """Empty UA → None (no platform keyword found)."""
        assert headers._platform("") is None

    def test_unknown_platform(self):
        """UA with no known platform keyword → None."""
        assert headers._platform("SomeBot/1.0") is None

    def test_ordering_android_before_linux(self):
        """Android UA contains 'linux' too, but 'android' is checked first."""
        ua = "Mozilla/5.0 (Linux; Android 13)"
        assert headers._platform(ua) == "Android"

    def test_chromeos_not_matched(self):
        """Chrome OS is not a listed platform → None."""
        ua = "Mozilla/5.0 (X11; CrOS x86_64 15236.80.0)"
        # "linux" is not in "cros x86_64" but "x11" doesn't help either
        # Actually CrOS doesn't contain any of the keywords except maybe none
        result = headers._platform(ua)
        # CrOS doesn't match any keyword → None
        assert result is None


# ===========================================================================
# _arch
# ===========================================================================


class TestArch:
    """Tests for _arch() function."""

    def test_x86_64(self):
        """'x86_64' in UA → 'x86'."""
        ua = "Mozilla/5.0 (X11; Linux x86_64)"
        assert headers._arch(ua) == "x86"

    def test_x64(self):
        """'x64' in UA → 'x86'."""
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        assert headers._arch(ua) == "x86"

    def test_win64(self):
        """'Win64' (case-insensitive) → 'x86'."""
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64)"
        assert headers._arch(ua) == "x86"

    def test_intel(self):
        """'Intel' in UA → 'x86'."""
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        assert headers._arch(ua) == "x86"

    def test_arm64(self):
        """'arm64' → 'arm'."""
        ua = "Mozilla/5.0 (Macintosh; arm64 Mac OS X)"
        assert headers._arch(ua) == "arm"

    def test_aarch64(self):
        """'aarch64' → 'arm'."""
        ua = "Mozilla/5.0 (Linux; aarch64)"
        assert headers._arch(ua) == "arm"

    def test_arm_in_ua(self):
        """'arm' substring → 'arm'."""
        ua = "Mozilla/5.0 (Linux; armv7l)"
        assert headers._arch(ua) == "arm"

    def test_case_insensitive(self):
        """Architecture detection is case-insensitive."""
        ua = "Mozilla/5.0 (X11; LINUX X86_64)"
        assert headers._arch(ua) == "x86"

    def test_empty_string(self):
        """Empty UA → None."""
        assert headers._arch("") is None

    def test_no_arch_keyword(self):
        """UA with no arch keywords → None."""
        assert headers._arch("SomeBot/1.0") is None

    def test_arm_checked_before_x86(self):
        """If both 'arm' and 'x86_64' appear, arm wins (checked first)."""
        ua = "arm device x86_64 compatible"
        assert headers._arch(ua) == "arm"

    def test_chrome_win_ua(self):
        """Standard Chrome Windows UA → x86."""
        assert headers._arch(CHROME_WIN_UA) == "x86"

    def test_chrome_mac_ua(self):
        """Standard Chrome Mac UA has 'Intel' → x86."""
        assert headers._arch(CHROME_MAC_UA) == "x86"


# ===========================================================================
# _client_hints
# ===========================================================================


class TestClientHints:
    """Tests for _client_hints() function."""

    def test_chrome_returns_hints(self):
        """Chrome browser should return client hints dict."""
        result = headers._client_hints("chrome", CHROME_WIN_UA)
        assert "Sec-Ch-Ua" in result
        assert "Sec-Ch-Ua-Mobile" in result
        assert "Sec-Ch-Ua-Platform" in result
        assert "Sec-Ch-Ua-Model" in result

    def test_edge_returns_hints(self):
        """Edge browser should return client hints."""
        result = headers._client_hints("edge", EDGE_UA)
        assert "Sec-Ch-Ua" in result

    def test_brave_returns_hints(self):
        """Brave browser should return client hints."""
        result = headers._client_hints("brave", CHROME_WIN_UA)
        assert "Sec-Ch-Ua" in result

    def test_chromium_returns_hints(self):
        """Chromium browser should return client hints."""
        result = headers._client_hints("chromium", CHROME_WIN_UA)
        assert "Sec-Ch-Ua" in result

    def test_firefox_returns_empty(self):
        """Firefox should return empty dict (non-chromium)."""
        result = headers._client_hints("firefox", FIREFOX_UA)
        assert result == {}

    def test_safari_returns_empty(self):
        """Safari (no chrome in UA) should return empty dict."""
        result = headers._client_hints("safari", SAFARI_MAC_UA)
        assert result == {}

    def test_none_browser_and_ua(self):
        """None inputs should return empty dict."""
        result = headers._client_hints(None, None)
        assert result == {}

    def test_empty_strings(self):
        """Empty strings should return empty dict."""
        result = headers._client_hints("", "")
        assert result == {}

    def test_chrome_but_firefox_in_ua_returns_empty(self):
        """If UA contains 'firefox', should return empty even with chrome browser."""
        result = headers._client_hints("chrome", "Firefox/121.0 Chrome/120")
        assert result == {}

    def test_safari_with_chrome_in_ua_returns_hints(self):
        """UA with both 'safari' and 'chrome' → chromium, returns hints."""
        result = headers._client_hints(None, CHROME_WIN_UA)
        assert "Sec-Ch-Ua" in result

    def test_ua_based_detection_edg(self):
        """'edg' in UA (without browser param) → chromium detection."""
        result = headers._client_hints(None, EDGE_UA)
        assert "Sec-Ch-Ua" in result

    def test_chrome_mobile_ua_returns_hints(self):
        """Chrome on mobile should return hints."""
        result = headers._client_hints("chrome", CHROME_ANDROID_UA)
        assert "Sec-Ch-Ua" in result

    def test_non_chromium_ua_returns_empty(self):
        """UA with no chromium indicators → empty."""
        result = headers._client_hints(None, "SomeBot/1.0")
        assert result == {}

    def test_hints_contain_expected_keys(self):
        """Returned dict should have exactly 4 keys."""
        result = headers._client_hints("chrome", CHROME_WIN_UA)
        assert set(result.keys()) == {
            "Sec-Ch-Ua",
            "Sec-Ch-Ua-Mobile",
            "Sec-Ch-Ua-Platform",
            "Sec-Ch-Ua-Model",
        }

    def test_sec_ch_ua_model_is_empty_string(self):
        """Sec-Ch-Ua-Model should always be empty string."""
        result = headers._client_hints("chrome", CHROME_WIN_UA)
        assert result["Sec-Ch-Ua-Model"] == ""


# ===========================================================================
# _sanitize
# ===========================================================================


class TestSanitize:
    """Tests for _sanitize() function."""

    def test_normal_string_unchanged(self):
        """Plain ASCII string passes through unchanged."""
        assert headers._sanitize("hello", field="test") == "hello"

    def test_none_input_returns_empty(self):
        """None → empty string."""
        assert headers._sanitize(None, field="test") == ""

    def test_empty_string(self):
        """Empty string → empty string."""
        assert headers._sanitize("", field="test") == ""

    def test_strip_spaces_true(self):
        """strip_spaces=True removes ALL whitespace."""
        assert (
            headers._sanitize("  hello  world  ", field="test", strip_spaces=True)
            == "helloworld"
        )

    def test_strip_spaces_false_strips_edges(self):
        """strip_spaces=False only strips leading/trailing whitespace."""
        assert (
            headers._sanitize("  hello  world  ", field="test", strip_spaces=False)
            == "hello  world"
        )

    def test_strip_spaces_default_false(self):
        """Default strip_spaces=False."""
        assert headers._sanitize("  hello  ", field="test") == "hello"

    def test_unicode_dashes_normalized(self):
        """Unicode dashes (U+2010–U+2014, U+2212) → ASCII '-'."""
        for char in "\u2010\u2011\u2012\u2013\u2014\u2212":
            result = headers._sanitize(f"hello{char}world", field="test")
            assert result == "hello-world", f"U+{ord(char):04X} not normalized"

    def test_unicode_quotes_normalized(self):
        """Unicode quotes → ASCII equivalents."""
        assert (
            headers._sanitize("hello\u2018world\u2019", field="test") == "hello'world'"
        )
        assert (
            headers._sanitize("hello\u201cworld\u201d", field="test") == 'hello"world"'
        )

    def test_non_breaking_space_normalized(self):
        """Non-breaking spaces → regular space."""
        assert headers._sanitize("hello\u00a0world", field="test") == "hello world"

    def test_zero_width_chars_removed(self):
        """Zero-width characters are removed."""
        for char in "\u200b\u200c\u200d\ufeff":
            result = headers._sanitize(f"hello{char}world", field="test")
            assert result == "helloworld", f"U+{ord(char):04X} not removed"

    def test_non_latin1_chars_stripped(self):
        """Characters outside Latin-1 range are stripped."""
        # CJK characters are outside Latin-1
        result = headers._sanitize("hello\u4e2dworld", field="test")
        assert result == "helloworld"

    def test_latin1_high_chars_preserved(self):
        """Characters in Latin-1 range (0-255) are preserved."""
        # \u00e9 = é, within Latin-1
        result = headers._sanitize("caf\u00e9", field="test")
        assert result == "caf\u00e9"

    def test_integer_input(self):
        """Non-string input is converted via str()."""
        assert headers._sanitize(12345, field="test") == "12345"

    def test_newlines_with_strip_spaces(self):
        """strip_spaces=True removes newlines too."""
        assert (
            headers._sanitize("hello\nworld\r\n", field="test", strip_spaces=True)
            == "helloworld"
        )

    def test_tabs_with_strip_spaces(self):
        """strip_spaces=True removes tabs."""
        assert (
            headers._sanitize("hello\tworld", field="test", strip_spaces=True)
            == "helloworld"
        )


# ===========================================================================
# build_http_headers
# ===========================================================================


class _MockProfile:
    """Mock ProxyProfile for header builder tests."""

    def __init__(
        self,
        user_agent=CHROME_WIN_UA,
        browser="chrome120",
        cf_cookies="",
        cf_clearance="",
    ):
        self.user_agent = user_agent
        self.browser = browser
        self.cf_cookies = cf_cookies
        self.cf_clearance = cf_clearance


class TestBuildHttpHeaders:
    """Tests for build_http_headers() function."""

    def _build(self, cookie_token="test-sso-token", **kwargs):
        """Helper to build headers with a mocked profile."""
        profile = _MockProfile()
        with (
            patch.object(headers, "_resolve_profile", return_value=profile),
            patch.object(headers, "_statsig_id", return_value="fake-statsig"),
        ):
            return headers.build_http_headers(cookie_token, **kwargs)

    def test_returns_dict(self):
        result = self._build()
        assert isinstance(result, dict)

    def test_contains_user_agent(self):
        result = self._build()
        assert "User-Agent" in result
        assert len(result["User-Agent"]) > 0

    def test_contains_accept(self):
        result = self._build()
        assert "Accept" in result

    def test_contains_cookie(self):
        result = self._build()
        assert "Cookie" in result

    def test_contains_origin(self):
        result = self._build()
        assert result["Origin"] == "https://grok.com"

    def test_contains_referer(self):
        result = self._build()
        assert result["Referer"] == "https://grok.com/"

    def test_contains_statsig_id(self):
        result = self._build()
        assert result["x-statsig-id"] == "fake-statsig"

    def test_contains_request_id(self):
        result = self._build()
        assert "x-xai-request-id" in result
        # UUID format check
        uuid_str = result["x-xai-request-id"]
        assert len(uuid_str) == 36
        assert uuid_str.count("-") == 4

    def test_json_content_type_defaults(self):
        """application/json → Accept: */*, Sec-Fetch-Dest: empty."""
        result = self._build()
        assert result["Content-Type"] == "application/json"
        assert result["Accept"] == "*/*"
        assert result["Sec-Fetch-Dest"] == "empty"

    def test_image_content_type(self):
        """image/jpeg → special Accept header, fd: document."""
        result = self._build(content_type="image/jpeg")
        assert result["Content-Type"] == "image/jpeg"
        assert "image/avif" in result["Accept"]
        assert result["Sec-Fetch-Dest"] == "document"

    def test_video_content_type(self):
        """video/mp4 → document dest."""
        result = self._build(content_type="video/mp4")
        assert result["Sec-Fetch-Dest"] == "document"

    def test_unknown_content_type(self):
        """Unknown CT → Accept: */*, fd: empty."""
        result = self._build(content_type="text/plain")
        assert result["Accept"] == "*/*"
        assert result["Sec-Fetch-Dest"] == "empty"

    def test_custom_origin_and_referer(self):
        result = self._build(
            origin="https://custom.com", referer="https://custom.com/page"
        )
        assert result["Origin"] == "https://custom.com"
        assert result["Referer"] == "https://custom.com/page"

    def test_same_origin_when_hosts_match(self):
        """Same host in origin and referer → Sec-Fetch-Site: same-origin."""
        result = self._build(origin="https://grok.com", referer="https://grok.com/chat")
        assert result["Sec-Fetch-Site"] == "same-origin"

    def test_same_site_when_hosts_differ(self):
        """Different hosts → Sec-Fetch-Site: same-site."""
        result = self._build(origin="https://a.com", referer="https://b.com/")
        assert result["Sec-Fetch-Site"] == "same-site"

    def test_sec_fetch_mode_is_cors(self):
        result = self._build()
        assert result["Sec-Fetch-Mode"] == "cors"

    def test_accept_encoding(self):
        result = self._build()
        assert "gzip" in result["Accept-Encoding"]
        assert "br" in result["Accept-Encoding"]

    def test_chromium_client_hints_added(self):
        """When profile browser is chrome, client hints should be present."""
        profile = _MockProfile(browser="chrome120")
        with (
            patch.object(headers, "_resolve_profile", return_value=profile),
            patch.object(headers, "_statsig_id", return_value="fake"),
        ):
            result = headers.build_http_headers("token")
        assert "Sec-Ch-Ua" in result

    def test_sso_token_in_cookie(self):
        """Cookie header should contain the SSO token."""
        result = self._build(cookie_token="my-sso-token")
        assert "sso=my-sso-token" in result["Cookie"]

    def test_with_cf_clearance_in_profile(self):
        """cf_clearance from profile should appear in Cookie."""
        profile = _MockProfile(cf_clearance="abc123")
        with (
            patch.object(headers, "_resolve_profile", return_value=profile),
            patch.object(headers, "_statsig_id", return_value="fake"),
        ):
            result = headers.build_http_headers("token")
        assert "cf_clearance=abc123" in result["Cookie"]

    def test_with_cf_cookies_in_profile(self):
        """cf_cookies from profile should appear in Cookie."""
        profile = _MockProfile(cf_cookies="cf_clearance=xyz; other=val")
        with (
            patch.object(headers, "_resolve_profile", return_value=profile),
            patch.object(headers, "_statsig_id", return_value="fake"),
        ):
            result = headers.build_http_headers("token")
        assert "cf_clearance=xyz" in result["Cookie"]

    def test_priority_header(self):
        result = self._build()
        assert result["Priority"] == "u=1, i"

    def test_baggage_header_present(self):
        result = self._build()
        assert "Baggage" in result
        assert "sentry" in result["Baggage"]


# ===========================================================================
# build_console_headers
# ===========================================================================


class TestBuildConsoleHeaders:
    """Tests for build_console_headers() function."""

    def _build(self, sso_token="test-token", **kwargs):
        """Helper to build console headers with a mocked profile."""
        profile = _MockProfile()
        with patch.object(headers, "_resolve_profile", return_value=profile):
            return headers.build_console_headers(sso_token, **kwargs)

    def test_returns_dict(self):
        result = self._build()
        assert isinstance(result, dict)

    def test_authorization_is_bearer_anonymous(self):
        """Console uses fixed 'Bearer anonymous' auth."""
        result = self._build()
        assert result["Authorization"] == "Bearer anonymous"

    def test_strips_sso_prefix(self):
        """'sso=' prefix should be stripped from token."""
        result = self._build(sso_token="sso=test-token-123")
        assert "sso=test-token-123" in result["Cookie"]

    def test_no_sso_prefix_preserved(self):
        """Token without 'sso=' prefix is used as-is."""
        result = self._build(sso_token="raw-token")
        assert "sso=raw-token" in result["Cookie"]

    def test_sso_rw_in_cookie(self):
        """Cookie should contain sso-rw with same value."""
        result = self._build(sso_token="my-token")
        assert "sso-rw=my-token" in result["Cookie"]

    def test_default_content_type(self):
        result = self._build()
        assert result["Content-Type"] == "application/json"

    def test_custom_content_type(self):
        result = self._build(content_type="text/plain")
        assert result["Content-Type"] == "text/plain"

    def test_origin_is_console(self):
        result = self._build()
        assert result["Origin"] == "https://console.x.ai"

    def test_referer_is_console(self):
        result = self._build()
        assert result["Referer"] == "https://console.x.ai/"

    def test_x_cluster_header(self):
        result = self._build()
        assert result["x-cluster"] == "https://us-east-1.api.x.ai"

    def test_accept_header(self):
        result = self._build()
        assert result["Accept"] == "*/*"

    def test_sec_fetch_dest(self):
        result = self._build()
        assert result["Sec-Fetch-Dest"] == "empty"

    def test_sec_fetch_mode(self):
        result = self._build()
        assert result["Sec-Fetch-Mode"] == "cors"

    def test_sec_fetch_site(self):
        result = self._build()
        assert result["Sec-Fetch-Site"] == "same-origin"

    def test_with_cf_clearance(self):
        """cf_clearance from profile should appear in Cookie."""
        profile = _MockProfile(cf_clearance="clearance-token")
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token")
        assert "cf_clearance=clearance-token" in result["Cookie"]

    def test_with_cf_cookies(self):
        """cf_cookies from profile should appear in Cookie."""
        profile = _MockProfile(cf_cookies="cf_clearance=abc; other=val")
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token")
        assert "cf_clearance=abc" in result["Cookie"]

    def test_user_agent_from_profile(self):
        """User-Agent should come from profile."""
        profile = _MockProfile(user_agent=CHROME_MAC_UA)
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token")
        assert "Macintosh" in result["User-Agent"]

    def test_empty_user_agent_uses_fallback(self):
        """When profile UA is empty, a fallback UA is used."""
        profile = _MockProfile(user_agent="")
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token")
        # The `or` operator in the code picks the fallback
        assert "Chrome/" in result["User-Agent"]

    def test_chromium_hints_added(self):
        """Client hints should be added for chromium browsers."""
        profile = _MockProfile(browser="chrome120")
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token")
        assert "Sec-Ch-Ua" in result

    def test_non_chromium_no_hints(self):
        """Non-chromium browser → no client hints."""
        profile = _MockProfile(browser="firefox121", user_agent=FIREFOX_UA)
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token")
        assert "Sec-Ch-Ua" not in result

    def test_sanitize_strips_unicode_from_token(self):
        """Token with unicode dashes should be normalized."""
        profile = _MockProfile()
        with patch.object(headers, "_resolve_profile", return_value=profile):
            result = headers.build_console_headers("token\u2014test")
        # The unicode em-dash should be normalized to '-'
        assert "sso=token-test" in result["Cookie"]
