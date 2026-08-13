import unittest
from email.message import Message
from typing import Any
from unittest import mock

from ui import web_server
from ui.web_server import _is_trusted_vpn_ip


def _build_handler(*, client_ip: str, path: str, headers: dict[str, str] | None = None) -> Any:
    handler = web_server._OperatorPanelHandler.__new__(web_server._OperatorPanelHandler)
    handler.client_address = (client_ip, 54321)
    handler.path = path
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    handler.headers = message
    handler._send_json = lambda status_code, payload: None  # type: ignore[method-assign]
    return handler


class TrustedVpnIpTests(unittest.TestCase):
    def test_tailscale_cgnat_range_is_trusted(self) -> None:
        for ip in ("100.64.0.1", "100.101.102.103", "100.127.255.254"):
            self.assertTrue(_is_trusted_vpn_ip(ip), ip)

    def test_tailscale_ula_prefix_is_trusted(self) -> None:
        self.assertTrue(_is_trusted_vpn_ip("fd7a:115c:a1e0::1"))

    def test_public_addresses_starting_with_100_are_not_trusted(self) -> None:
        for ip in ("100.0.0.1", "100.200.10.5", "100.63.255.255", "100.128.0.1"):
            self.assertFalse(_is_trusted_vpn_ip(ip), ip)

    def test_other_ula_prefixes_are_not_trusted(self) -> None:
        self.assertFalse(_is_trusted_vpn_ip("fd00::1"))

    def test_invalid_values(self) -> None:
        for value in ("", "   ", "not-an-ip", "100.", None):
            self.assertFalse(_is_trusted_vpn_ip(value))


class AccessPolicyTests(unittest.TestCase):
    def test_localhost_always_allowed(self) -> None:
        handler = _build_handler(client_ip="127.0.0.1", path="/api/status")
        with mock.patch.object(web_server, "PANEL_TOKEN", "s" * 32):
            self.assertTrue(handler._enforce_access_policy("/api/status"))

    def test_vpn_client_without_token_is_rejected(self) -> None:
        handler = _build_handler(client_ip="100.101.102.103", path="/api/status")
        with mock.patch.object(web_server, "PANEL_TOKEN", "s" * 32):
            self.assertFalse(handler._enforce_access_policy("/api/status"))

    def test_vpn_client_with_token_header_is_allowed(self) -> None:
        token = "s" * 32
        handler = _build_handler(
            client_ip="100.101.102.103",
            path="/api/toggle_scan",
            headers={"X-Panel-Token": token},
        )
        with mock.patch.object(web_server, "PANEL_TOKEN", token):
            self.assertTrue(handler._enforce_access_policy("/api/toggle_scan"))

    def test_token_via_query_and_cookie(self) -> None:
        token = "s" * 32
        with mock.patch.object(web_server, "PANEL_TOKEN", token):
            query_handler = _build_handler(client_ip="100.64.0.9", path=f"/?token={token}")
            self.assertTrue(query_handler._enforce_access_policy("/"))
            cookie_handler = _build_handler(
                client_ip="100.64.0.9",
                path="/api/status",
                headers={"Cookie": f"sqe_panel_token={token}"},
            )
            self.assertTrue(cookie_handler._enforce_access_policy("/api/status"))

    def test_wrong_token_is_rejected(self) -> None:
        with mock.patch.object(web_server, "PANEL_TOKEN", "s" * 32):
            handler = _build_handler(
                client_ip="100.64.0.9",
                path="/api/status",
                headers={"X-Panel-Token": "x" * 32},
            )
            self.assertFalse(handler._enforce_access_policy("/api/status"))

    def test_status_route_is_protected_for_lan_clients(self) -> None:
        with mock.patch.object(web_server, "PANEL_TOKEN", "s" * 32):
            handler = _build_handler(client_ip="192.168.1.44", path="/api/status")
            self.assertFalse(handler._enforce_access_policy("/api/status"))

    def test_authorized_ip_still_needs_token(self) -> None:
        with mock.patch.object(web_server, "PANEL_TOKEN", "s" * 32), mock.patch.object(
            web_server, "is_ip_authorized", return_value=True
        ):
            without_token = _build_handler(client_ip="192.168.1.44", path="/api/status")
            self.assertFalse(without_token._enforce_access_policy("/api/status"))
            with_token = _build_handler(
                client_ip="192.168.1.44",
                path="/api/status",
                headers={"X-Panel-Token": "s" * 32},
            )
            self.assertTrue(with_token._enforce_access_policy("/api/status"))

    def test_token_cookie_is_set_only_for_valid_query_token(self) -> None:
        token = "s" * 32
        with mock.patch.object(web_server, "PANEL_TOKEN", token):
            valid = _build_handler(client_ip="100.64.0.9", path=f"/?token={token}")
            self.assertTrue(any(name == "Set-Cookie" for name, _ in valid._panel_token_cookie_headers()))
            invalid = _build_handler(client_ip="100.64.0.9", path="/?token=wrong")
            self.assertEqual(invalid._panel_token_cookie_headers(), ())
            no_query = _build_handler(
                client_ip="100.64.0.9",
                path="/",
                headers={"X-Panel-Token": token},
            )
            self.assertEqual(no_query._panel_token_cookie_headers(), ())


if __name__ == "__main__":
    unittest.main()
