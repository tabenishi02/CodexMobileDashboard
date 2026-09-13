import runpy
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch, Mock


class BackupLauncherTests(unittest.TestCase):
    def test_hidden_command_matches_manual_and_returns_failure(self):
        main = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/run_backup_hidden.pyw'))['main']
        with patch('subprocess.run', return_value=Mock(returncode=2)) as run, patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True):
            self.assertEqual(2, main(['--python','python.exe','--config','config with spaces.ini']))
            self.assertEqual(['python.exe','-m','tools.backup_pair','--config','config with spaces.ini'], run.call_args.args[0])
            self.assertEqual(0x08000000, run.call_args.kwargs['creationflags'])
        with patch('subprocess.run', side_effect=OSError('private data')), patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True):
            self.assertEqual(2, main(['--python','missing.exe','--config','x']))
