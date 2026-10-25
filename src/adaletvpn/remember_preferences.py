import json
import os
from pathlib import Path
import tempfile


def preference_path():
    base = Path(os.environ.get('XDG_CONFIG_HOME', ''))
    if not base.is_absolute(): base = Path.home() / '.config'
    return base / 'adaletvpn' / 'preferences.json'


def read_remember(pin=False):
    try:
        value = json.loads(preference_path().read_text())
        return isinstance(value,dict) and value.get('remember') is True and (not pin or value.get('remember_pin') is True)
    except (OSError, ValueError):
        return False


def write_remember(enabled, pin=False):
    path = preference_path()
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.preferences-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump({'remember':bool(enabled), 'remember_pin':bool(enabled and pin)},f)
            f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)
