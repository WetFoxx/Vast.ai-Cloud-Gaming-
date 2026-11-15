#!/usr/bin/env python3
"""wolf_session.py — единые функции сессионного допуска к очистке и метаданных Wolf."""
import json
import os
import re
import subprocess

def get_run_id(run_id_file='/run/wolf/run_id'):
    try:
        with open(run_id_file, 'r', encoding='utf-8') as f:
            v = f.read().strip()
            return v if v else None
    except Exception:
        return None

def get_env_iid(iid_file='/run/wolf/iid'):
    v = os.environ.get('VI')
    if v and str(v).strip():
        return str(v).strip()
    try:
        with open(iid_file, 'r', encoding='utf-8') as f:
            c = f.read().strip()
            return c if c else None
    except Exception:
        return None

def get_env_r(env_file='/etc/wolf/env'):
    if os.environ.get('R'):
        v = str(os.environ['R']).strip()
        if v:
            return v
    if os.path.isfile(env_file):
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if 'R=' in line:
                        m = re.search(r'\bR=["\']?([^"\'\s]+)["\']?', line)
                        if m and m.group(1).strip():
                            return m.group(1).strip()
        except Exception:
            pass
    return ''

def get_storage_folder_id(r_val=None, rc_cmd=None, fid_path='/run/wolf/storage_folder_id'):
    if r_val is None:
        r_val = get_env_r()
    if not r_val or ':' not in r_val:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    rem, _, fol = r_val.partition(':')
    fol = fol.strip('/')
    if not rem or not fol:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    if rc_cmd is None:
        rc_cmd = os.environ.get('WOLF_RCLONE_CMD', 'rclone')
    try:
        res = subprocess.run([rc_cmd, 'lsjson', '-M', '--stat', r_val],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            d = json.loads(res.stdout or '{}')
            fid = str(d.get('ID') or d.get('id') or '').strip()
            if fid:
                try:
                    os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                    tmp = f'{fid_path}.tmp.{os.getpid()}'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(fid)
                    os.replace(tmp, fid_path)
                except Exception:
                    pass
                return fid
        else:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
    except Exception:
        try:
            os.unlink(fid_path)
        except Exception:
            pass
        return None
    try:
        res = subprocess.run([rc_cmd, 'lsjson', '-M', '--dirs-only', '--max-depth', '1', f'{rem}:'],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            for item in json.loads(res.stdout or '[]'):
                if str(item.get('Name') or '').strip() == fol:
                    fid = str(item.get('ID') or item.get('id') or '').strip()
                    if fid:
                        try:
                            os.makedirs(os.path.dirname(fid_path), exist_ok=True)
                            tmp = f'{fid_path}.tmp.{os.getpid()}'
                            with open(tmp, 'w', encoding='utf-8') as f:
                                f.write(fid)
                            os.replace(tmp, fid_path)
                        except Exception:
                            pass
                        return fid
        else:
            try:
                os.unlink(fid_path)
            except Exception:
                pass
            return None
    except Exception:
        pass
    try:
        os.unlink(fid_path)
    except Exception:
        pass
    return None

def is_cleanup_authorized(grant_file='/run/wolf/cleanup_grant.json', run_id_val=None, iid_val=None, folder_id_val=None):
    """Единая проверка допуска к очистке во всех слоях Wolf."""
    cur_run_id = (run_id_val if run_id_val is not None else get_run_id()) or ''
    cur_iid = (iid_val if iid_val is not None else get_env_iid()) or ''
    cur_fid = (folder_id_val if folder_id_val is not None else get_storage_folder_id()) or ''
    if not cur_run_id or not cur_iid or not cur_fid:
        return False
    if not os.path.isfile(grant_file):
        return False
    try:
        with open(grant_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        if data.get('status') != 'granted':
            return False
        g_run = str(data.get('run_id') or '').strip()
        g_iid = str(data.get('iid') or '').strip()
        g_fid = str(data.get('folder_id') or '').strip()
        if not g_run or not g_iid or not g_fid:
            return False
        if g_run != cur_run_id or g_iid != cur_iid or g_fid != cur_fid:
            return False
        return True
    except Exception:
        return False


def is_cleanup_enabled(enabled_val=None, grant_file='/run/wolf/cleanup_grant.json', enabled_file='/run/wolf/cleanup_enabled'):
    """Проверка явного включения очистки (по умолчанию ВСЕГДА False)."""
    if enabled_val is not None:
        return bool(enabled_val)
    env_v = os.environ.get("VG_STORAGE_CLEANUP_ENABLED", "").lower().strip()
    if env_v in ("1", "true", "yes"):
        return True
    if os.path.isfile(enabled_file):
        try:
            with open(enabled_file, "r", encoding="utf-8") as f:
                val = f.read().strip().lower()
                if val in ("1", "true", "yes", ""):
                    return True
        except Exception:
            pass
    if os.path.isfile(grant_file):
        try:
            with open(grant_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("enabled") is True:
                    return True
        except Exception:
            pass
    return False

