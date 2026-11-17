"""Remove only the four authorized, unchanged, fully integrated remote refs."""
import json
import os
from pathlib import Path
import subprocess

repo=os.environ['GITHUB_REPOSITORY'];head=os.environ['GITHUB_SHA']
refs=json.loads(Path(__file__).with_name('branch-cleanup.json').read_text())
remove=[]
for branch,expected in refs.items():
    if branch in {'main','develop'}:raise ValueError('Permanent branch must be retained')
    result=subprocess.run(['gh','api',f'repos/{repo}/git/ref/heads/{branch}'],capture_output=True,text=True)
    if result.returncode:
        if 'HTTP 404' in result.stderr:continue
        raise RuntimeError('Cannot inspect branch: '+branch)
    actual=json.loads(result.stdout)['object']['sha']
    if actual!=expected:raise RuntimeError('Branch moved; retaining all refs: '+branch)
    subprocess.run(['git','merge-base','--is-ancestor',expected,head],check=True)
    remove.append((branch,expected))
if remove:
    # All leases and ancestry checks must hold; do not partially delete the set.
    command=['git','push','--atomic','origin']
    command.extend(f'--force-with-lease=refs/heads/{branch}:{sha}' for branch,sha in remove)
    command.extend(':refs/heads/'+branch for branch,sha in remove)
    subprocess.run(command,check=True)
print('Authorized temporary branches cleaned; main, develop and tags retained.')
