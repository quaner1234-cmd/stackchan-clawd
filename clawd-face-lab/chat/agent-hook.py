"""Native Codex hook: observation only; never return an approval decision."""
import json
import sys
from agent_status import emit

def main():
    try:
        p=json.load(sys.stdin)
        kind=p.get('hook_event_name','')
        state={'UserPromptSubmit':'running','PermissionRequest':'waiting',
               'PostToolUse':'running','Stop':'completed','Interrupt':'cancelled',
               'SessionEnd':'idle'}.get(kind)
        if state and p.get('session_id'):emit('codex',p['session_id'],state)
    except Exception:pass  # Notification failures must never break the agent.
    print('{}')

if __name__=='__main__':main()
