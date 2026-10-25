import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

from adaletvpn.pkcs11 import PKCS11Certificates


class DriverDiscoveryTests(unittest.TestCase):
    def test_explicit_missing_driver_rejected(self):
        with self.assertRaises(ValueError):
            PKCS11Certificates('/missing/driver.so')

    def test_missing_akis_uses_system_providers(self):
        with patch('adaletvpn.pkcs11.Path.is_file', return_value=False):
            provider = PKCS11Certificates()
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (b'', b'')
        with patch('asyncio.create_subprocess_exec', return_value=process) as launch:
            asyncio.run(provider._run('--list-all-certs'))
        self.assertNotIn('--provider=None', launch.call_args.args)
        self.assertEqual(launch.call_args.args[0], 'p11tool')

    def test_explicit_path_with_spaces_kept_as_single_argument(self):
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder) / 'card driver.so'
            module.touch()
            provider = PKCS11Certificates(str(module))
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b'', b'')
            with patch('asyncio.create_subprocess_exec', return_value=process) as launch:
                asyncio.run(provider._run('--list-all-certs'))
            self.assertIn('--provider=' + str(module), launch.call_args.args)


class DebianPackageTests(unittest.TestCase):
    def test_package_contents_launcher_and_worker_import(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['python3', str(root / 'tools/build_deb.py')], check=True,
                       stdout=subprocess.DEVNULL)
        arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
        package = root / 'dist' / arch / 'AdaletVPN.deb'
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            subprocess.run(['dpkg-deb', '-x', str(package), folder], check=True)
            names = [str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()]
            for forbidden in ('network-snapshots', 'uyap-terminal', 'trials', '__pycache__',
                              'font-provenance', '/usr/local', 'README.md', 'INSTALL.md'):
                self.assertFalse(any(forbidden in name for name in names), forbidden)
            app = stage / 'usr/lib/adaletvpn'
            metadata = ET.parse(stage / 'usr/share/metainfo/com.adalettekno.AdaletVPN.metainfo.xml').getroot()
            self.assertEqual(subprocess.check_output(
                ['dpkg-deb', '-f', str(package), 'Version'], text=True).strip(), '1.0.0')
            depends = subprocess.check_output(
                ['dpkg-deb', '-f', str(package), 'Depends'], text=True)
            self.assertIn('python3-pil', [part.strip() for part in depends.split(',')])
            self.assertEqual(metadata.find('releases/release').get('version'), '1.0.0')
            self.assertEqual(metadata.findtext('developer/name'), 'Adalet Tekno')
            self.assertEqual(metadata.findtext('description/p'),
                             'AdaletVPN, Linux üzerinde e-imza ile UYAP VPN bağlantısı kurmanızı sağlar.')
            self.assertEqual(metadata.findtext('launchable'), 'com.adalettekno.AdaletVPN.desktop')
            self.assertTrue((stage / 'usr/share/applications' / metadata.findtext('launchable')).is_file())
            for name in ('adaletvpn-text.ttf',):
                self.assertEqual((app / 'data/fonts' / name).read_bytes(),
                                 (root / 'data/fonts' / name).read_bytes())
            launcher = (stage / 'usr/bin/adaletvpn').read_text().replace(
                '/usr/lib/adaletvpn', str(app))
            launcher = launcher.replace('raise SystemExit(main())',
                'import subprocess\nsubprocess.run([sys.executable, "-c", '
                '\'import adaletvpn.auth_worker, adaletvpn.nm_worker, adaletvpn.vault_worker\'], check=True)')
            probe = stage / 'probe.py'
            probe.write_text(launcher)
            env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
            subprocess.run(['python3', '-I', str(probe)], cwd=folder, env=env, check=True)
            registration = (stage / 'usr/lib/NetworkManager/VPN/nm-adaletvpn-service.name').read_text()
            self.assertIn('program=/usr/lib/adaletvpn/nm-adaletvpn-service', registration)
            self.assertNotIn('/usr/local', registration)
            self.assertTrue((stage / 'usr/bin/adaletvpn').stat().st_mode & 0o111)
