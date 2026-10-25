"""Reject mixed diagnostic components before CLI, Tk, or P1 file access.

All module origins and paths below are fictional canaries. No file/network
selection, Tk window, Tailscale process, or private data is used.
"""
from contextlib import redirect_stdout, redirect_stderr
import importlib
import io
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import identity_diagnostics
import private_launcher as entry
import tailscale_transport as transport
import phone_launcher
import test_phone as phone_tests

BUILD_CODE = 'diagnostics_identity_v4'
MISMATCH_CODE = 'diagnostic_component_mismatch'
COMPONENTS = ('identity_diagnostics', 'tailscale_transport', 'phone_launcher')


def components():
    modules = {}
    for name in COMPONENTS:
        module = ModuleType(name)
        module.DIAGNOSTIC_PROTOCOL = 4
        module.__file__ = 'C:\\PRIVATE_SOURCE_PATH_CANARY\\' + name + '.py'
        modules[name] = module
    modules['phone_launcher'].main = Mock(return_value=0)
    return modules


class RuntimeDiagnosticProtocolTests(unittest.TestCase):
    def invoke(self, modules):
        output = io.StringIO(); errors = io.StringIO()
        with patch.dict(sys.modules, modules), redirect_stdout(output), redirect_stderr(errors):
            result = entry.main()
        return result, output.getvalue(), errors.getvalue()

    def assert_rejected(self, modules):
        result, output, errors = self.invoke(modules)
        self.assertEqual(result, 1)
        self.assertEqual(output.splitlines(), [BUILD_CODE, MISMATCH_CODE])
        self.assertEqual(errors, '')
        self.assertNotIn('PRIVATE', output)
        self.assertNotIn('C:', output)
        modules['phone_launcher'].main.assert_not_called()

    def test_actual_components_publish_protocol_four_and_fixed_build_code(self):
        for module in (identity_diagnostics, transport, phone_launcher):
            self.assertEqual(module.DIAGNOSTIC_PROTOCOL, 4)
        self.assertEqual(transport.BUILD_CODE, BUILD_CODE)

    def test_complete_runtime_calls_only_the_real_mode_ui_entry(self):
        modules = components()
        result, output, errors = self.invoke(modules)
        self.assertEqual(result, 0)
        modules['phone_launcher'].main.assert_called_once_with(real=True)
        self.assertTrue(set(output.splitlines()) <= {BUILD_CODE})
        self.assertEqual(errors, '')

    def test_each_old_or_wrong_type_protocol_is_rejected_without_private_origins(self):
        for name in COMPONENTS:
            for old in (0, 2, 3, '4', None):
                with self.subTest(component=name, protocol=old):
                    modules = components(); modules[name].DIAGNOSTIC_PROTOCOL = old
                    self.assert_rejected(modules)

    def test_each_missing_protocol_is_rejected_without_launching_tk(self):
        for name in COMPONENTS:
            with self.subTest(component=name):
                modules = components(); del modules[name].DIAGNOSTIC_PROTOCOL
                self.assert_rejected(modules)

    def test_missing_component_imports_are_sanitized(self):
        for name in COMPONENTS:
            with self.subTest(component=name):
                modules = components()
                ui = modules['phone_launcher']
                modules[name] = None  # importlib raises ModuleNotFoundError.
                result, output, errors = self.invoke(modules)
                self.assertEqual(result, 1)
                self.assertEqual(output.splitlines(), [BUILD_CODE, MISMATCH_CODE])
                self.assertEqual(errors, '')
                ui.main.assert_not_called()

    def test_import_exception_does_not_echo_source_paths_or_exception_text(self):
        output = io.StringIO(); errors = io.StringIO()
        failure = ImportError('PRIVATE_IMPORT_CANARY C:\\PRIVATE_SOURCE_PATH_CANARY\\component.py')
        with patch.object(importlib, 'import_module', side_effect=failure), redirect_stdout(output), redirect_stderr(errors):
            result = entry.main()
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue().splitlines(), [BUILD_CODE, MISMATCH_CODE])
        self.assertEqual(errors.getvalue(), '')

    def test_transport_component_validation_uses_live_identity_protocol(self):
        transport.validate_diagnostic_components()
        with patch.object(identity_diagnostics, 'DIAGNOSTIC_PROTOCOL', 2), self.assertRaises(transport.TailnetError) as error:
            transport.validate_diagnostic_components()
        self.assertEqual(error.exception.code, MISMATCH_CODE)

    def test_share_rejects_mixed_identity_protocol_before_cli_or_p1_actions(self):
        cli = Mock()
        share = transport.PhoneShare(cli)
        with patch.object(identity_diagnostics, 'DIAGNOSTIC_PROTOCOL', 2), \
             patch.object(transport, 'create_server', side_effect=AssertionError('must not read P1')) as fixture, \
             patch.object(transport, 'create_private_server', side_effect=AssertionError('must not read private files')) as private, \
             patch.object(transport.subprocess, 'Popen', side_effect=AssertionError('must not launch Serve')) as popen, \
             self.assertRaises(transport.TailnetError) as error:
            share.start('PRIVATE_P1_PATH_CANARY', phone_tests.DEVICE,
                        snapshot_file='PRIVATE_SNAPSHOT_PATH_CANARY', market_files=['PRIVATE_MARKET_PATH_CANARY'])
        self.assertEqual(error.exception.code, MISMATCH_CODE)
        cli.inspect.assert_not_called(); cli.config.assert_not_called()
        cli.whois.assert_not_called(); cli.command.assert_not_called()
        fixture.assert_not_called(); private.assert_not_called(); popen.assert_not_called()
        self.assertIsNone(share.server)
        self.assertNotIn('PRIVATE', str(error.exception))


if __name__ == '__main__': unittest.main()
