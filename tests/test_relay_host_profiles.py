"""Tests for per-target-host relay credential resolution (``relay_hosts``)."""

import unittest

from config import resolve_relay_credentials


class ResolveRelayCredentialsTests(unittest.TestCase):
    def test_global_fallback_when_relay_hosts_missing(self):
        config = {
            "relay_username": "root",
            "relay_password": "global-pw",
            "relay_prompt_timeout": 6,
        }
        result = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(result["username"], "root")
        self.assertEqual(result["password"], "global-pw")
        self.assertEqual(result["prompt_timeout"], 6.0)

    def test_exact_host_match_overrides_globals(self):
        config = {
            "relay_username": "root",
            "relay_password": "global-pw",
            "relay_prompt_timeout": 6,
            "relay_hosts": {
                "10.1.1.100": {"username": "admin", "password": "host-pw"},
            },
        }
        result = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(result["username"], "admin")
        self.assertEqual(result["password"], "host-pw")
        self.assertEqual(result["prompt_timeout"], 6.0)

    def test_other_host_falls_back_to_globals(self):
        config = {
            "relay_username": "root",
            "relay_password": "global-pw",
            "relay_hosts": {
                "10.1.1.100": {"username": "admin", "password": "host-pw"},
            },
        }
        result = resolve_relay_credentials(config, "10.1.1.101")
        self.assertEqual(result["username"], "root")
        self.assertEqual(result["password"], "global-pw")

    def test_per_host_timeout_override(self):
        config = {
            "relay_hosts": {
                "10.1.1.100": {"username": "admin", "password": "pw",
                               "relay_prompt_timeout": 12},
            },
        }
        result = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(result["prompt_timeout"], 12.0)

    def test_partial_override_keeps_global_password(self):
        config = {
            "relay_username": "root",
            "relay_password": "global-pw",
            "relay_hosts": {
                "10.1.1.100": {"username": "admin"},
            },
        }
        result = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(result["username"], "admin")
        self.assertEqual(result["password"], "global-pw")

    def test_wildcard_entry_used_when_no_exact_match(self):
        config = {
            "relay_username": "root",
            "relay_password": "global-pw",
            "relay_hosts": {
                "10.1.1.100": {"username": "admin", "password": "exact-pw"},
                "*": {"username": "fallback", "password": "wild-pw"},
            },
        }
        exact = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(exact["password"], "exact-pw")
        other = resolve_relay_credentials(config, "10.1.1.200")
        self.assertEqual(other["username"], "fallback")
        self.assertEqual(other["password"], "wild-pw")

    def test_empty_and_malformed_entries(self):
        config = {
            "relay_username": "root",
            "relay_password": "global-pw",
            "relay_prompt_timeout": "bad",
            "relay_hosts": {"10.1.1.100": "not-a-dict", "": {}},
        }
        result = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(result["username"], "root")
        self.assertEqual(result["password"], "global-pw")
        self.assertEqual(result["prompt_timeout"], 6.0)

    def test_no_host_and_no_globals_yields_blank_credentials(self):
        result = resolve_relay_credentials({"relay_hosts": {}}, None)
        self.assertEqual(result["username"], "")
        self.assertEqual(result["password"], "")
        self.assertEqual(result["prompt_timeout"], 6.0)

    def test_timeout_floored_at_three_seconds(self):
        config = {
            "relay_hosts": {"*": {"relay_prompt_timeout": 1}},
        }
        result = resolve_relay_credentials(config, "10.1.1.100")
        self.assertEqual(result["prompt_timeout"], 3.0)


if __name__ == "__main__":
    unittest.main()
