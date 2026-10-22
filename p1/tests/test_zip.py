"""ZIP/no-Git compatibility checks using only temporary fictional files."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

P1 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P1))
import check_private


class ZipCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve(strict=True)
        self.source = self.base / '源码ZIP'
        (self.source / 'p1').mkdir(parents=True)
        shutil.copy2(P1 / 'check_private.py', self.source / 'p1' / 'check_private.py')
        (self.source / 'README.md').write_text('虚构测试源码。', encoding='utf-8')

    def run_guard(self, all_files=False):
        output = io.StringIO()
        with patch.object(check_private, 'REPO', self.source), contextlib.redirect_stdout(output):
            result = check_private.check(all_files)
        return result, output.getvalue()

    def write(self, relative, content='fictional test content'):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def no_git_environment(self):
        return dict(os.environ, PATH='', PYTHONIOENCODING='cp1252')

    def test_zip_without_git_metadata_uses_full_scan_for_both_modes(self):
        # Even an installed Git must not inspect an unrelated parent index.
        with patch('check_private.subprocess.check_output', side_effect=AssertionError('Do not call parent Git')):
            for all_files in (False, True):
                with self.subTest(all_files=all_files):
                    result, output = self.run_guard(all_files)
                    self.assertEqual(result, 0)
                    self.assertIn('文件系统全量扫描（非Git暂存区检查）', output)
                    self.assertIn('检查通过', output)

    def test_missing_git_executable_uses_labelled_scan(self):
        (self.source / '.git').mkdir()
        self.write('.git/metadata.json')
        with patch('check_private.subprocess.check_output', side_effect=FileNotFoundError()):
            result, output = self.run_guard(True)
        self.assertEqual(result, 0)
        self.assertIn('非Git暂存区检查', output)

    def test_git_staged_and_all_commands_and_rules_are_preserved(self):
        (self.source / '.git').write_text('gitdir: fictional', encoding='utf-8')
        for all_files, arguments in [
            (False, ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']),
            (True, ['git', 'ls-files', '-z']),
        ]:
            with self.subTest(all_files=all_files), patch(
                'check_private.subprocess.check_output',
                return_value=b'config.example.json\0p1/goose_research/market.py\0',
            ) as command:
                result, output = self.run_guard(all_files)
                self.assertEqual(result, 0)
                self.assertNotIn('文件系统全量扫描', output)
                self.assertEqual(command.call_args.args[0], arguments)
                self.assertEqual(command.call_args.kwargs['cwd'], self.source)
        with patch('check_private.subprocess.check_output', return_value=b'notes.csv\0'):
            self.assertEqual(self.run_guard()[0], 1)

    def test_other_git_failure_is_not_reported_as_success(self):
        (self.source / '.git').mkdir()
        failure = subprocess.CalledProcessError(128, ['git'], stderr=b'fictional-private-detail')
        with patch('check_private.subprocess.check_output', side_effect=failure):
            result, output = self.run_guard(True)
        self.assertEqual(result, 1)
        self.assertIn('Git检查失败', output)
        self.assertNotIn('fictional-private-detail', output)
        self.assertNotIn('文件系统全量扫描', output)

    def test_same_forbidden_suffixes_and_paths_apply_to_zip(self):
        cases = ['artifact' + suffix.upper() for suffix in check_private.FORBIDDEN]
        cases += ['snapshots/x.py', 'reports/x.md', 'private/x.txt', 'backups/x.py']
        for relative in cases:
            with self.subTest(relative=relative):
                path = self.write(relative)
                result, output = self.run_guard(True)
                self.assertEqual(result, 1)
                self.assertNotIn('fictional test content', output)
                path.unlink()
                for parent in path.parents:
                    if parent == self.source:
                        break
                    parent.rmdir()

    def test_only_root_example_files_are_allowed(self):
        for relative in check_private.ALLOW:
            self.write(relative)
        self.assertEqual(self.run_guard(True)[0], 0)
        self.write('nested/config.example.json')
        self.assertEqual(self.run_guard(True)[0], 1)

    def test_gitignore_and_tool_directories_do_not_hide_private_files(self):
        self.write('.gitignore', 'work/\n.venv/\n__pycache__/\n*.csv\n')
        for relative in ['work/notes.csv', '.venv/notes.csv', '__pycache__/notes.csv', 'ignored.csv']:
            with self.subTest(relative=relative):
                path = self.write(relative)
                self.assertEqual(self.run_guard(True)[0], 1)
                path.unlink()

    def test_empty_private_directory_is_not_a_safe_source_artifact(self):
        (self.source / 'reports').mkdir()
        self.assertEqual(self.run_guard(True)[0], 1)

    def test_unreadable_directory_fails_closed_without_error_details(self):
        with patch('check_private.os.scandir', side_effect=PermissionError('fictional-secret')):
            result, output = self.run_guard(True)
        self.assertEqual(result, 1)
        self.assertIn('无法完成文件系统全量扫描', output)
        self.assertNotIn('fictional-secret', output)

    def test_unreadable_file_fails_closed_without_reading_contents(self):
        with patch('check_private.os.open', side_effect=PermissionError('fictional-secret')):
            result, output = self.run_guard(True)
        self.assertEqual(result, 1)
        self.assertNotIn('fictional-secret', output)

    def test_unknown_entry_type_and_windows_reparse_tag_are_rejected(self):
        with patch('check_private.stat.S_ISREG', return_value=False):
            self.assertEqual(self.run_guard(True)[0], 1)
        info = SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
        self.assertTrue(check_private._link_or_reparse(info))
        unknown = SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0, st_file_attributes=0x400)
        self.assertTrue(check_private._link_or_reparse(unknown))

    def test_source_symlink_is_not_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        self.write('safe.py')
        link = self.source / 'linked-source'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit this test to create a symbolic link')
        self.assertEqual(self.run_guard(True)[0], 1)

    def test_real_subprocess_without_git_and_cp1252_output(self):
        # Exercise the missing-executable branch with actual PATH, not a mock.
        (self.source / '.git').mkdir()
        result = subprocess.run(
            [sys.executable, str(self.source / 'p1' / 'check_private.py'), '--all'],
            cwd=self.source, env=self.no_git_environment(), capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout.decode('utf-8')
        self.assertIn('文件系统全量扫描（非Git暂存区检查）', text)
        self.assertIn('检查通过', text)
        self.write('notes.csv')
        result = subprocess.run(
            [sys.executable, str(self.source / 'p1' / 'check_private.py')],
            cwd=self.source, env=self.no_git_environment(), capture_output=True,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('拒绝', result.stdout.decode('utf-8'))

    def test_actual_cli_init_template_collect_from_zip_without_git(self):
        shutil.copytree(P1 / 'goose_research', self.source / 'p1' / 'goose_research',
                        ignore=shutil.ignore_patterns('__pycache__'))
        data = self.base / '本地虚构测试数据'
        env = self.no_git_environment()

        def invoke(arguments, program=None):
            command = [sys.executable, '-m', 'goose_research'] if program is None else [sys.executable, '-c', program]
            return subprocess.run(command + ['--data-dir', str(data)] + arguments,
                                  cwd=self.source / 'p1', env=env, capture_output=True)

        initialized = invoke(['init', '--confirm-local-storage'])
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        template = invoke(['template'])
        self.assertEqual(template.returncode, 0, template.stderr)
        self.assertIn(str(data), template.stdout.decode('utf-8'))
        self.assertTrue((data / 'holdings-template.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
        # Call the actual CLI, collect, save, and validators; only HTTP transport
        # is replaced by an explicit offline failure. No real request is sent.
        program = '''
import sys
from urllib.error import URLError
from goose_research import market
original = market.fetch
def offline(*args, **kwargs):
    def unavailable(*args, **kwargs):
        raise URLError('fictional offline test')
    kwargs['opener'] = unavailable
    return original(*args, **kwargs)
market.fetch = offline
from goose_research.__main__ import main
raise SystemExit(main(sys.argv[1:]))
'''
        collected = invoke(['collect', '600900.SH'], program)
        self.assertEqual(collected.returncode, 0, collected.stderr)
        records = list((data / 'market').glob('*.json'))
        self.assertEqual(len(records), 1)
        document = json.loads(records[0].read_text(encoding='utf-8'))
        self.assertEqual(document['status'], 'unavailable')
        self.assertEqual(document['rows'], [])
        self.assertEqual(document['error_type'], 'URLError')
        self.assertNotIn('fictional offline test', records[0].read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
