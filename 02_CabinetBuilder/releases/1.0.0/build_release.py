"""Verify the immutable 1.0.0 source manifest and reproduce its release ZIP."""
from pathlib import Path,PurePosixPath
import argparse,ast,hashlib,json,zipfile

ROOT=Path(__file__).resolve().parent
NAME='Lime-Integrated-Toolbox-1.0.0.zip'

def build(output,check_expected=True):
    source=ROOT/'package';manifest=json.loads((source/'MANIFEST.json').read_text(encoding='utf-8'))
    assert manifest['version']=='1.0.0' and manifest['channel']=='stable'
    expected=manifest['files'];actual={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and p.name!='MANIFEST.json' and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    assert actual==set(expected),'Source file list differs from the signed-off manifest'
    for name,digest in expected.items():
        path=PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
        file=source/name;assert hashlib.sha256(file.read_bytes()).hexdigest()==digest,name
        if file.suffix=='.py':ast.parse(file.read_text(encoding='utf-8'),filename=name)
        if file.suffix=='.json':json.loads(file.read_text(encoding='utf-8'))
    tree=ast.parse((source/'lime_integrated_tools_ingetrazo.py').read_text(encoding='utf-8'))
    assert any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets) and isinstance(n.value,ast.Constant) and n.value.value=='1.0.0' for n in tree.body)
    evidence=json.loads((source/'RELEASE_VALIDATION.json').read_text(encoding='utf-8'))
    assert evidence['full_regression']['failed']==0 and evidence['full_regression']['passed']==357
    assert evidence['windows_application_verified'] is False
    output=Path(output);output.mkdir(parents=True,exist_ok=True);package=output/NAME
    with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in sorted([*expected,'MANIFEST.json']):
            info=zipfile.ZipInfo(name,date_time=(2026,11,25,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.create_system=3;info.external_attr=0o100644<<16
            archive.writestr(info,(source/name).read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=6)
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        for name,digest in expected.items():assert hashlib.sha256(archive.read(name)).hexdigest()==digest
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    if check_expected:
        metadata=json.loads((ROOT/'release.json').read_text(encoding='utf-8'));assert metadata['zip_sha256']==digest,'Release archive digest differs'
    (output/(NAME+'.sha256')).write_text(digest+'  '+NAME+'\n',encoding='utf-8')
    print(json.dumps({'archive':str(package),'sha256':digest,'files':len(expected)+1,'bytes':package.stat().st_size},ensure_ascii=False))
    return package,digest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='dist');args=parser.parse_args();build(args.output)
