from copy import deepcopy
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from investment.serve_bridge import ensure_private_route, close_owned_route, check_private_route

HOST = "fictional-device.example.ts.net"
STATUS = {"BackendState": "Running", "Self": {"DNSName": HOST+"."}}


def route():
    return {"TCP": {"10001": {"HTTPS": True}},
        "Web": {HOST+":10001": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9394"}}}}}


class PrivateRouteTests(unittest.TestCase):
    def test_readonly_recheck_blocks_a_borrowed_route_that_disappeared(self):
        with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, {}]),
                patch("investment.serve_bridge.subprocess.Popen") as start):
            self.assertFalse(check_private_route(HOST)["ready"])
            start.assert_not_called()

    def test_existing_safe_route_only_reads_never_starts_or_stops(self):
        for configuration in (route(), {"Foreground": {"fixture": route()}}):
            with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, configuration]) as reads,
                    patch("investment.serve_bridge.subprocess.Popen") as start):
                handle = ensure_private_route(HOST+":10001")
                self.assertTrue(handle["ready"])
                self.assertEqual(handle["status"], "reused")
                self.assertEqual(close_owned_route(handle)["code"], "EXISTING_PRIVATE_ROUTE_UNCHANGED")
                start.assert_not_called()
                self.assertEqual(reads.call_count, 2)

    def test_conflict_funnel_and_sibling_host_never_override(self):
        wrong_target = route()
        wrong_target["Web"][HOST+":10001"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9999"
        funnel = route()
        funnel["AllowFunnel"] = {HOST+":10001": True}
        sibling = route()
        sibling["Web"]["other.example.ts.net:10001"] = deepcopy(sibling["Web"][HOST+":10001"])
        for configuration in (wrong_target, funnel, sibling):
            with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, configuration]),
                    patch("investment.serve_bridge.subprocess.Popen") as start):
                self.assertFalse(ensure_private_route(HOST)["ready"])
                start.assert_not_called()

    def test_host_mismatch_or_unsupported_port_has_no_process(self):
        with patch("investment.serve_bridge.tailscale_json", return_value=STATUS), patch("investment.serve_bridge.subprocess.Popen") as start:
            self.assertFalse(ensure_private_route("other.example.ts.net")["ready"])
            self.assertFalse(ensure_private_route(HOST, 9393)["ready"])
            self.assertFalse(ensure_private_route("https://bad.example.ts.net")["ready"])
            start.assert_not_called()

    def test_new_foreground_route_verified_then_only_owned_child_stopped(self):
        child = Mock()
        child.poll.side_effect = [None, None, 0]
        with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, {}, route(), {}]),
                patch("investment.serve_bridge.subprocess.Popen", return_value=child) as start,
                patch("investment.serve_bridge.time.sleep")):
            handle = ensure_private_route(HOST)
            self.assertTrue(handle["ready"])
            command = start.call_args.args[0]
            self.assertEqual(command[1:], ["serve", "--https=10001", "http://127.0.0.1:9394"])
            self.assertNotIn("--bg", command)
            self.assertEqual(close_owned_route(handle)["status"], "stopped")
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=3)

    def test_changed_other_route_rolls_back_only_own_child(self):
        child = Mock()
        child.poll.side_effect = [None, None, 0]
        changed = route()
        changed["TCP"]["9999"] = {"HTTPS": True}
        with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, {}, changed]),
                patch("investment.serve_bridge.subprocess.Popen", return_value=child),
                patch("investment.serve_bridge.time.sleep")):
            result = ensure_private_route(HOST)
            self.assertFalse(result["ready"])
            self.assertEqual(result["code"], "PRIVATE_ROUTE_OTHER_CONFIGURATION_CHANGED")
            child.terminate.assert_called_once()

    def test_failed_start_or_unknown_status_never_claims_url(self):
        with patch("investment.serve_bridge.tailscale_json", side_effect=RuntimeError("PRIVATE_DETAIL")):
            result = ensure_private_route(HOST)
            self.assertFalse(result["ready"])
            self.assertNotIn("PRIVATE_DETAIL", str(result))
        child = Mock()
        child.poll.return_value = 1
        with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, {}]),
                patch("investment.serve_bridge.subprocess.Popen", return_value=child)):
            result = ensure_private_route(HOST)
            self.assertFalse(result["ready"])
            child.terminate.assert_not_called()

    def test_cleanup_uncertain_retains_exact_child_ownership(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = subprocess.TimeoutExpired("fictional-child", 3)
        changed = route()
        changed["TCP"]["9999"] = {"HTTPS": True}
        with (patch("investment.serve_bridge.tailscale_json", side_effect=[STATUS, {}, changed]),
                patch("investment.serve_bridge.subprocess.Popen", return_value=child),
                patch("investment.serve_bridge.time.sleep")):
            handle = ensure_private_route(HOST)
        self.assertFalse(handle["ready"])
        self.assertIs(handle["ownedProcess"], child)
        self.assertEqual(handle["code"], "PRIVATE_ROUTE_OWNED_CLEANUP_UNVERIFIED")
        self.assertEqual(close_owned_route(handle)["status"], "unverified")
        self.assertIs(handle["ownedProcess"], child)


if __name__ == "__main__":
    unittest.main()
