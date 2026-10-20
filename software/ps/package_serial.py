"""生成离线串口包；不连接串口、不访问板卡，依赖仅来自本SoundSight仓库。

python software/ps/package_serial.py
输出release/output/soundsight-pynq-serial.zip；实际传输/运行见串口部署.txt。
"""
import argparse
import base64
import gzip
import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[2]
SOLVER_REF='61e7303331641ec2353f89088b060aa81ffd6110'
LOCAL_FILES=(
    'software/common/fpga_stm32_abi_frozen.h',
    'software/common/s1/doa_solver.c',
    'software/common/s1/doa_solver.h',
    'software/common/s1/test_doa_solver.c',
    'software/ps/ss_solver_bridge.c',
    'software/ps/build_solver.sh',
    'software/ps/ps_client.py',
    'software/ps/run_capture.py',
    'software/ps/run_smp.py',
    'software/ps/运行说明.txt',
    'software/ps/串口部署.txt',
    'software/tests/test_smp.py',
)
SOLVER_FILES=(
    'software/tests/test_solver_migration.py',
    'software/tests/solver_migration_reference.c',
    'software/common/s1/PS-MIGRATION.md',
    'document/evidence/ps-solver-migration-20261020/PC-RESULT.json',
)


def git_bytes(*args):
    result=subprocess.run(['git','--no-lazy-fetch','-c','safe.directory='+ROOT.as_posix(),*args],
                          cwd=ROOT,capture_output=True)
    if result.returncode:
        raise RuntimeError('无法读取本仓库候选；先执行 git fetch origin pull/15/head:refs/remotes/origin/pr/15')
    return result.stdout


def make_payload(files):
    """固定归档元数据；保持仓库相对路径，板上无需旧仓库或主机绝对路径。"""
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w',format=tarfile.PAX_FORMAT) as archive:
        for name,data in sorted(files.items()):
            entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o644
            archive.addfile(entry,io.BytesIO(data))
    compressed=io.BytesIO()
    with gzip.GzipFile(fileobj=compressed,mode='wb',mtime=0,filename='') as handle:
        handle.write(raw.getvalue())
    return compressed.getvalue()


def serial_commands(payload):
    """每块独立覆盖，重发同一步不会重复追加；每条小于旧方法3800字节限制。"""
    encoded=base64.b64encode(payload).decode('ascii')
    chunks=[encoded[i:i+2048] for i in range(0,len(encoded),2048)]
    if len(chunks)>9999:raise ValueError('串口包过大')
    commands=['ss_stage="$HOME/soundsight-serial-inbox"',
              'ss_dest="$HOME/soundsight-pynq"',
              'mkdir -p "$ss_stage"']
    for index,chunk in enumerate(chunks):
        commands.append("printf '%s' '"+chunk+"' > \"$ss_stage/%04d.b64\" && printf 'CHUNK_%04d_OK\\n'"%(index,index))
    # 只拼接本包声明的块数；旧暂存目录里多余的块不会混入。
    commands.append(': > "$ss_stage/payload.tar.gz" && (ss_i=0; while [ "$ss_i" -lt %d ]; do cat "$ss_stage/$(printf \'%%04d\' "$ss_i").b64" || exit 1; ss_i=$((ss_i+1)); done) > "$ss_stage/payload.b64" && base64 -d "$ss_stage/payload.b64" > "$ss_stage/payload.tar.gz"'%len(chunks))
    commands.append('test "$(wc -c < "$ss_stage/payload.tar.gz")" -eq %d && gzip -t "$ss_stage/payload.tar.gz" && printf \'PAYLOAD_OK bytes=%d\\n\''%(len(payload),len(payload)))
    commands.append('if [ -e "$ss_dest" ]; then printf \'DEST_EXISTS: set ss_dest to a new directory\\n\'; false; elif ! test "$(wc -c < "$ss_stage/payload.tar.gz")" -eq %d || ! gzip -t "$ss_stage/payload.tar.gz"; then printf \'BAD_PAYLOAD\\n\'; false; else mkdir "$ss_dest" && tar -xzf "$ss_stage/payload.tar.gz" -C "$ss_dest" && printf \'UNPACK_OK\\n\'; fi'%len(payload))
    if any(len(line.encode('ascii'))>=3800 or '\n' in line or '\r' in line for line in commands):
        raise ValueError('串口命令超过单行限制')
    return commands


def build(output,solver_ref=SOLVER_REF):
    resolved=git_bytes('rev-parse','--verify',solver_ref+'^{commit}').decode().strip()
    files={name:(ROOT/name).read_bytes().replace(b'\r\n',b'\n') for name in LOCAL_FILES}
    # B的交付原样取Git对象，不在C目录维护第二份算法或测试源。
    for name in SOLVER_FILES:files[name]=git_bytes('show',resolved+':'+name)
    files['LICENSE']=git_bytes('show','HEAD:LICENSE')
    # 仅供同包在Windows复跑B测试；目标Linux路径不依赖此工具发现助手。
    files['tools/tool_config.py']=git_bytes('show','HEAD:tools/tool_config.py')
    contents=('SoundSight offline serial software package\n'
              'Same Linux/PYNQ: CPU1 calls C, CPU0 controls/saves.\n'
              'B PR #15 source: '+resolved+'\n'
              'Current ABI: 2D azimuth/horizontal only; ARM/board NOT_RUN.\n'
              'No bit/HWH, ARM.so, Windows DLL or CPU1 bare-metal ELF included.\n\n'
              +'\n'.join('%s  %d bytes'%(name,len(data)) for name,data in sorted(files.items()))+'\n')
    files['PACKAGE.txt']=contents.encode('utf-8')
    payload=make_payload(files);commands=serial_commands(payload)
    members={'payload.tar.gz':payload,
             'serial_commands.txt':('\n'.join(commands)+'\n').encode('ascii'),
             'copy_serial_step.ps1':(ROOT/'software/ps/copy_serial_step.ps1').read_bytes(),
             '串口部署.txt':files['software/ps/串口部署.txt'],
             'CONTENTS.txt':contents.encode('utf-8')}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(members.items()):
            entry=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o644<<16
            archive.writestr(entry,data)
    print('SERIAL_PACKAGE_READY bytes=%d steps=%d max_command_bytes=%d'%
          (len(payload),len(commands),max(len(c) for c in commands)))
    print(output)
    return payload,commands


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'release/output/soundsight-pynq-serial.zip')
    parser.add_argument('--solver-ref',default=SOLVER_REF,help='本地已获取的B算法测试候选，不自动联网')
    args=parser.parse_args(argv);build(args.output,args.solver_ref)


if __name__=='__main__':main()
