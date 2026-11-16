"""Reproducible stdlib-only packager for the immutable test.4 release scope."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]

def build(output):
    record=json.loads((HERE/'release.json').read_text(encoding='utf-8'))
    files={}
    for name,entry in record['files'].items():
        data=(ROOT/entry['source']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('Release source changed: '+name)
        files[name]=data
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    files['BUILD.json']=(json.dumps({**record,'git_commit':commit},ensure_ascii=False,indent=2)+'\n').encode()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    package=output/'IngeTrazo-D5-LiveSync-0.9.10.zip'
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,11,16,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,data)
    with zipfile.ZipFile(package) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC failed')
        for name,data in files.items():
            if z.read(name)!=data:raise ValueError('ZIP content mismatch')
    sha=hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix('.zip.sha256').write_text(sha+'  '+package.name+'\n',encoding='utf-8')
    print(package,sha)
    return package

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='dist');args=parser.parse_args();build(args.output)
