"""Hermes approval observer. Actual turn boundaries come from its native log."""
import importlib.util
from pathlib import Path

def register(ctx):
    path=Path(r'D:/Projects/StackChan-prep/clawd-face-lab/chat/agent_status.py')
    # Import only the small standard-library observer, never the robot/Cloud.
    import sys
    sys.path.insert(0,str(path.parent))
    from agent_status import emit
    def waiting(**kw):
        try:
            sid=kw.get('session_key') or kw.get('session_id')
            if sid:emit('hermes',sid,'waiting')
        except Exception:pass
    def resumed(**kw):
        try:
            sid=kw.get('session_key') or kw.get('session_id')
            if sid:emit('hermes',sid,'running')
        except Exception:pass
    for name in ('pre_approval_request','on_human_input_request'):ctx.register_hook(name,waiting)
    for name in ('post_approval_response','on_human_input_resolved'):ctx.register_hook(name,resumed)
