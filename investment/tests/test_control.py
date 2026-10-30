"""Control-window integration with fake UI, subprocess and bearer values only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from investment import control


HOST = "fictional-device.fictional-tailnet.ts.net"
OLD = "fictional-old-link-" + "A" * 48
NEW = "fictional-new-link-" + "B" * 48


def result(value=None, *, status=0, raw=None):
    return SimpleNamespace(returncode=status, stdout=raw if raw is not None else json.dumps(value).encode("utf-8"))


class PrivateHostTests(unittest.TestCase):
    def call(self, value):
        with patch.object(control.subprocess, "run", return_value=result(value)) as process:
            answer = control.private_host()
        self.assertEqual([r"C:\Program Files\Tailscale\tailscale.exe", "status", "--json"], process.call_args.args[0])
        self.assertTrue(process.call_args.kwargs["capture_output"])
        self.assertEqual(8, process.call_args.kwargs["timeout"])
        return answer

    def test_host_is_discovered_at_runtime_with_fixed_private_port(self):
        self.assertEqual(HOST + ":10001", self.call({"BackendState": "Running", "Self": {"DNSName": HOST + "."}}))

    def test_not_connected_states_do_not_expose_a_link(self):
        for status in ("Stopped", "Starting", "NeedsLogin", "NeedsMachineAuth", "NoState", "", None):
            with self.subTest(status=status):
                self.assertIsNone(self.call({"BackendState": status, "Self": {"DNSName": HOST}}))

    def test_unknown_or_wrongly_typed_status_fields_fail_closed(self):
        for document in (None, [], "not-an-object", {}, {"BackendState": "Running"},
                         {"BackendState": "Running", "Self": None},
                         {"BackendState": "Running", "Self": []},
                         {"BackendState": "Running", "Self": "invalid"},
                         {"BackendState": "Running", "Self": {"DNSName": None}},
                         {"BackendState": "Running", "Self": {"DNSName": 123}}):
            with self.subTest(shape=type(document).__name__):
                self.assertIsNone(self.call(document))

    def test_untrusted_host_string_is_not_turned_into_phone_url(self):
        for name in ("", "example.com", "https://" + HOST, HOST + "/path", HOST + ":443",
                     "user@" + HOST, HOST + "\r\nInjected: value", HOST + "?key=anything"):
            with self.subTest(name=name):
                self.assertIsNone(self.call({"BackendState": "Running", "Self": {"DNSName": name}}))

    def test_errors_invalid_json_and_large_responses_do_not_crash_or_log_identity(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            for response in (result({}, status=1), result(raw=b"not json"), result(raw=b" " * (4 * 1024 * 1024 + 1))):
                with patch.object(control.subprocess, "run", return_value=response):
                    self.assertIsNone(control.private_host())
            for exception in (OSError("fake executable unavailable"), subprocess.TimeoutExpired("fake", 8)):
                with patch.object(control.subprocess, "run", side_effect=exception):
                    self.assertIsNone(control.private_host())
        self.assertEqual("", output.getvalue())
        self.assertEqual("", errors.getvalue())


class FakeWidget:
    def pack(self, **kwargs):
        return None


class FakeStatus:
    def __init__(self, **kwargs):
        self.value = kwargs.get("value")

    def set(self, value):
        self.value = value


class ControlFlowTests(unittest.TestCase):
    def exercise(self, rotation_error=False, phone_host=HOST + ":10001", paused=False):
        buttons, protocols, clipboard = {}, {}, []
        fake_server = SimpleNamespace(access_key=OLD, serve_forever=Mock(), shutdown=Mock(), server_close=Mock())
        fake_window = Mock()
        fake_window.clipboard_append.side_effect = clipboard.append
        fake_window.protocol.side_effect = lambda key, callback: protocols.update({key: callback})

        def button(*args, **kwargs):
            buttons[kwargs["text"]] = kwargs["command"]
            return FakeWidget()

        def mainloop():
            buttons["复制 iPhone 私有报告地址"]()
            buttons["重置手机链接（撤销旧链接）"]()
            buttons["复制 iPhone 私有报告地址"]()
            protocols["WM_DELETE_WINDOW"]()

        fake_window.mainloop.side_effect = mainloop
        with patch("investment.operations.pause_state",return_value={'paused':paused}), \
                patch.object(control, "private_host", return_value=phone_host), \
                patch.object(control.tk, "Tk", return_value=fake_window), \
                patch.object(control.tk, "StringVar", side_effect=FakeStatus), \
                patch.object(control.ttk, "Frame", return_value=FakeWidget()), \
                patch.object(control.ttk, "Label", return_value=FakeWidget()), \
                patch.object(control.ttk, "Button", side_effect=button), \
                patch.object(control.threading, "Thread") as threads, \
                patch.object(control.webbrowser, "open") as browser, \
                patch("investment.server.make_server", return_value=fake_server) as factory, \
                patch("investment.serve_bridge.ensure_private_route", return_value={"ready": True, "status": "reused", "code": "PHONE_ROUTE_REUSED", "ownedProcess": None}) as bridge, \
                patch("investment.serve_bridge.check_private_route", return_value={"ready": True, "status": "verified", "code": "PHONE_ROUTE_VERIFIED", "ownedProcess": None}), \
                patch("investment.serve_bridge.close_owned_route") as close_bridge, \
                patch("investment.access.load_or_create_access_key", return_value=OLD) as load, \
                patch("investment.access.rotate_access_key", side_effect=ValueError("fake safe failure") if rotation_error else None, return_value=NEW) as rotate:
            control.open_control(Path("D:/fictional-application"), port=9394)
        if phone_host:
            bridge.assert_called_once_with(phone_host, port=9394)
        else:
            bridge.assert_not_called()
        close_bridge.assert_called_once()
        return fake_server, clipboard, factory, load, rotate, threads, browser

    def test_initial_launch_uses_persisted_key_and_rotation_immediately_replaces_phone_link(self):
        server, clipboard, factory, load, rotate, threads, browser = self.exercise()
        self.assertEqual(OLD, factory.call_args.kwargs["access_key"])
        self.assertEqual([HOST + ":10001"], factory.call_args.kwargs["allowed_hosts"])
        self.assertEqual(["https://" + HOST + ":10001"], factory.call_args.kwargs["allowed_origins"])
        self.assertEqual(["https://" + HOST + ":10001/investment/#key=" + OLD,
                          "https://" + HOST + ":10001/investment/#key=" + NEW], clipboard)
        self.assertEqual(NEW, server.access_key)
        load.assert_called_once()
        rotate.assert_called_once()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        # Mock thread.start means automatic analysis/network never executes here.
        self.assertEqual(2, threads.call_count)
        self.assertTrue(any(NEW in call.args[0] for call in browser.call_args_list))

    def test_failed_rotation_leaves_server_key_and_existing_phone_link_usable(self):
        server, clipboard, *_ = self.exercise(rotation_error=True)
        self.assertEqual(OLD, server.access_key)
        self.assertEqual(clipboard[0], clipboard[1])

    def test_paused_startup_serves_page_without_automatic_analysis_thread(self):
        server,clipboard,factory,load,rotate,threads,browser=self.exercise(paused=True)
        self.assertEqual(1,threads.call_count)
        self.assertTrue(browser.called)

    def test_no_connected_private_host_does_not_make_up_phone_url(self):
        server, clipboard, *_ = self.exercise(phone_host=None)
        self.assertEqual([], clipboard)
        server.shutdown.assert_called_once()


class ControlCleanupTests(unittest.TestCase):
    def exercise_fault(self, fault):
        server = SimpleNamespace(access_key=OLD, serve_forever=Mock(), shutdown=Mock(), server_close=Mock())
        window = Mock()
        callbacks = {}
        window.protocol.side_effect = lambda name, callback: callbacks.update({name:callback})
        if fault == "widget": window.title.side_effect = RuntimeError("FICTIONAL_WIDGET_ERROR")
        if fault == "shutdown": server.shutdown.side_effect = RuntimeError("FICTIONAL_SHUTDOWN_ERROR")
        if fault == "socket_close": server.server_close.side_effect = RuntimeError("FICTIONAL_CLOSE_ERROR")
        bridge = {"ready":True, "status":"started", "code":"FICTIONAL_ROUTE", "ownedProcess":object()}
        attempts = []
        def stop_owned(handle):
            attempts.append(1)
            if fault == "bridge_once" and len(attempts) == 1:
                raise RuntimeError("FICTIONAL_ROUTE_CLOSE_ERROR")
            handle["ownedProcess"] = None
            return {"status":"stopped"}
        def mainloop():
            callbacks["WM_DELETE_WINDOW"]()
            if fault == "bridge_once": callbacks["WM_DELETE_WINDOW"]()
        window.mainloop.side_effect = mainloop
        with (patch.object(control, "private_host", return_value=HOST+":10001"),
                patch.object(control.tk, "Tk", side_effect=RuntimeError("FICTIONAL_TK_ERROR") if fault == "tk" else None, return_value=window),
                patch.object(control.tk, "StringVar", side_effect=FakeStatus),
                patch.object(control.ttk, "Frame", return_value=FakeWidget()),
                patch.object(control.ttk, "Label", return_value=FakeWidget()),
                patch.object(control.ttk, "Button", return_value=FakeWidget()),
                patch.object(control.threading, "Thread"),
                patch.object(control.webbrowser, "open", side_effect=RuntimeError("FICTIONAL_BROWSER_ERROR") if fault == "browser" else None),
                patch("investment.server.make_server", return_value=server),
                patch("investment.access.load_or_create_access_key", return_value=OLD),
                patch("investment.serve_bridge.ensure_private_route", return_value=bridge),
                patch("investment.serve_bridge.check_private_route", side_effect=AssertionError("No copy in cleanup tests")),
                patch("investment.serve_bridge.close_owned_route", side_effect=stop_owned) as closer):
            try: control.open_control(Path("D:/fictional-application"))
            except RuntimeError as error:
                self.assertIn("FICTIONAL_", str(error))
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        self.assertIsNone(bridge["ownedProcess"])
        self.assertEqual(closer.call_count, 2 if fault == "bridge_once" else 1)
        if fault == "tk": window.destroy.assert_not_called()
        else: window.destroy.assert_called_once()

    def test_tk_init_failure_stops_own_route_and_server(self):
        self.exercise_fault("tk")

    def test_widget_and_browser_failures_cleanup_started_resources(self):
        for fault in ("widget", "browser"):
            with self.subTest(fault=fault): self.exercise_fault(fault)

    def test_one_close_failure_does_not_skip_other_resources(self):
        for fault in ("shutdown", "socket_close", "bridge_once"):
            with self.subTest(fault=fault): self.exercise_fault(fault)


if __name__ == "__main__":
    unittest.main()
