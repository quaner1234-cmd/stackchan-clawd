"""Add only observer hooks/plugins; preserve RTK, notify and approval policy.

Requires ordinary-user profile write access. Codex reviews its new hooks itself;
this installer deliberately cannot edit trust hashes or auto-approve anything.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent
BACKUP=ROOT/'backups'/'2026-10-22-agent-adapters'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rollback',action='store_true');args=parser.parse_args()
    manifest=BACKUP/'manifest.json'
    if args.rollback:
        rows=json.loads(manifest.read_text(encoding='utf-8'))
        for row in rows:
            dest=Path(row['target'])
            if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=row['installed_sha256']:
                raise RuntimeError('Adapter changed since installation; refusing to overwrite: '+str(dest))
        for row in rows:
            dest=Path(row['target'])
            if row['existed']:shutil.copy2(BACKUP/row['backup'],dest)
            else:dest.unlink()
        print('Observer files restored. Restart the affected agent to unload its plugin.');return
    if manifest.exists():
        for row in json.loads(manifest.read_text(encoding='utf-8')):
            dest=Path(row['target'])
            if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=row['installed_sha256']:
                raise RuntimeError('Adapter differs from installation; inspect before reinstalling: '+str(dest))
        print('Already installed; original backup retained.');return
    home=Path.home();codex=home/'.codex'/'hooks.json'
    hooks=json.loads(codex.read_text(encoding='utf-8')) if codex.exists() else {'hooks':{}}
    command=f'"{sys.executable}" "{ROOT / "agent-hook.py"}"'
    for event in ('UserPromptSubmit','PermissionRequest','PostToolUse','Stop','Interrupt','SessionEnd'):
        entries=hooks.setdefault('hooks',{}).setdefault(event,[])
        if not any(h.get('command')==command for entry in entries for h in entry.get('hooks',[])):
            entries.append({'hooks':[{'type':'command','command':command,'timeout':3}]})
    hermes=Path(os.environ['LOCALAPPDATA'])/'hermes'/'plugins'/'xiaoke-status'
    changes=[(codex,(json.dumps(hooks,ensure_ascii=False,indent=2)+'\n').encode()),
        (home/'.config'/'opencode'/'plugins'/'xiaoke-status.js',(ROOT/'adapters'/'opencode-status.js').read_bytes()),
        (hermes/'__init__.py',(ROOT/'adapters'/'hermes-status'/'__init__.py').read_bytes()),
        (hermes/'plugin.yaml',(ROOT/'adapters'/'hermes-status'/'plugin.yaml').read_bytes())]
    # Do not overwrite another integration with the same filename.
    for dest,_ in changes[1:]:
        if dest.exists():raise RuntimeError('Existing adapter needs manual review: '+str(dest))
    BACKUP.mkdir(parents=True,exist_ok=True);rows=[]
    for i,(dest,data) in enumerate(changes):
        existed=dest.exists();name=f'{i}-{dest.name}'
        if existed:shutil.copy2(dest,BACKUP/name)
        rows.append(dict(target=str(dest),existed=existed,backup=name,installed_sha256=hashlib.sha256(data).hexdigest()))
    # Save intent/restore evidence before changing the user profile.
    manifest.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
    for dest,data in changes:
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        if dest.read_bytes()!=data:raise RuntimeError('Adapter readback failed: '+str(dest))
    print('Installed and read back observer files. RTK/notify/approval policy retained.')
    print('Codex: review new hooks in /hooks; installer did not modify trust.')
    print('OpenCode/Hermes: plugins load when their backend next starts.')

if __name__=='__main__':main()
