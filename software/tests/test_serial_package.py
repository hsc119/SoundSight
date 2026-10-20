"""PC串口包验证：重组字节、真实shell解包/错误检出、剪贴板辅助只读模式。"""
import base64
import gzip
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'software/ps'))
import package_serial as pack


class SerialPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.base=Path(cls.tmp.name)
        cls.archive=cls.base/'serial.zip'
        cls.payload,cls.commands=pack.build(cls.archive)
        cls.bash=shutil.which('bash')
        windows_bash=Path('C:/Program Files/Git/bin/bash.exe')
        if sys.platform=='win32' and windows_bash.is_file():cls.bash=str(windows_bash)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def shell(self,folder,commands):
        if not self.bash:self.skipTest('Bash unavailable; shell transport NOT_RUN')
        folder.mkdir(exist_ok=True)
        # 仅把测试初始目录改为独立临时PWD，不改变系统HOME或用户板端目录。
        isolated=[line.replace('$HOME/','$PWD/') for line in commands]
        return subprocess.run([self.bash,'--noprofile','--norc','-s'],input='set -eu\n'+'\n'.join(isolated)+'\n',
                              cwd=folder,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    def chunk_range(self):
        return range(3,len(self.commands)-3)
    def test_payload_is_complete_and_has_no_target_binaries(self):
        with tarfile.open(fileobj=io.BytesIO(self.payload),mode='r:gz') as archive:
            names=archive.getnames()
            self.assertTrue(set(pack.LOCAL_FILES+pack.SOLVER_FILES)<=set(names))
            self.assertTrue({'LICENSE','tools/tool_config.py','PACKAGE.txt'}<=set(names))
            self.assertFalse(any(name.endswith(('.dll','.elf','.so','.bit','.hwh')) for name in names))
            self.assertEqual(archive.extractfile('software/ps/build_solver.sh').read(),
                             (ROOT/'software/ps/build_solver.sh').read_bytes().replace(b'\r\n',b'\n'))
    def test_short_ascii_commands_restore_exact_bytes(self):
        parts=[]
        for index in self.chunk_range():
            line=self.commands[index]
            self.assertLess(len(line.encode('ascii')),3800)
            self.assertNotIn('\n',line)
            # 独立按命令的printf参数取块，并按Base64标准恢复原归档。
            parts.append(line.split("'")[3])
        self.assertEqual(base64.b64decode(''.join(parts),validate=True),self.payload)
        self.assertEqual(gzip.decompress(self.payload)[:10],b'LICENSE\x00\x00\x00')
    def test_real_shell_transfers_and_unpacks(self):
        folder=self.base/'success';result=self.shell(folder,self.commands)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PAYLOAD_OK',result.stdout);self.assertIn('UNPACK_OK',result.stdout)
        self.assertEqual((folder/'soundsight-serial-inbox/payload.tar.gz').read_bytes(),self.payload)
        app=folder/'soundsight-pynq/software/ps/run_smp.py'
        self.assertEqual(app.read_bytes(),(ROOT/'software/ps/run_smp.py').read_bytes().replace(b'\r\n',b'\n'))
    def test_resending_one_chunk_is_idempotent(self):
        commands=self.commands[:4]+[self.commands[3]]+self.commands[4:]
        folder=self.base/'repeat';result=self.shell(folder,commands)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((folder/'soundsight-serial-inbox/payload.tar.gz').read_bytes(),self.payload)
    def test_missing_chunk_is_rejected(self):
        folder=self.base/'missing';result=self.shell(folder,self.commands[:3]+self.commands[4:])
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((folder/'soundsight-pynq').exists())
    def test_missing_chunk_cannot_unpack_stale_valid_archive(self):
        folder=self.base/'stale';stage=folder/'soundsight-serial-inbox';stage.mkdir(parents=True)
        (stage/'payload.tar.gz').write_bytes(self.payload)
        result=self.shell(folder,self.commands[:3]+self.commands[4:])
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((folder/'soundsight-pynq').exists())
    def test_corruption_is_rejected(self):
        commands=self.commands[:]
        commands[3]=commands[3].replace("'H4s", "'AAAA",1)
        self.assertNotEqual(commands[3],self.commands[3])
        folder=self.base/'corrupt';result=self.shell(folder,commands)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((folder/'soundsight-pynq').exists())
    def test_existing_destination_is_preserved(self):
        folder=self.base/'exists';folder.mkdir();destination=folder/'soundsight-pynq';destination.mkdir()
        marker=destination/'original.txt';marker.write_text('keep',encoding='utf-8')
        # shell()创建目录，因此为已建测试目录直接调用同一命令序列。
        if not self.bash:self.skipTest('Bash unavailable')
        commands=[line.replace('$HOME/','$PWD/') for line in self.commands]
        result=subprocess.run([self.bash,'--noprofile','--norc','-s'],input='set -eu\n'+'\n'.join(commands)+'\n',
                              cwd=folder,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertIn('DEST_EXISTS',result.stdout)
        self.assertEqual(marker.read_text(),'keep')
        self.assertFalse((destination/'software').exists())
    def test_clipboard_helper_readonly_mode(self):
        powershell=shutil.which('powershell') or shutil.which('pwsh')
        if not powershell:self.skipTest('PowerShell unavailable')
        commands_path=self.base/'serial_commands.txt'
        commands_path.write_text('\n'.join(self.commands)+'\n',encoding='ascii')
        result=subprocess.run([powershell,'-NoProfile','-ExecutionPolicy','Bypass','-File',
                               str(ROOT/'software/ps/copy_serial_step.ps1'),'-Path',str(commands_path),
                               '-Step','4','-NoClipboard'],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),self.commands[3])
    def test_zip_has_pc_instructions(self):
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(set(archive.namelist()),{'payload.tar.gz','serial_commands.txt',
                             'copy_serial_step.ps1','串口部署.txt','CONTENTS.txt'})
            self.assertEqual(archive.read('payload.tar.gz'),self.payload)
            self.assertIn('ARM-RESULT.json',archive.read('串口部署.txt').decode('utf-8'))


if __name__=='__main__':unittest.main()
