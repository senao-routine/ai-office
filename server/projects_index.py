#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mac 上の Claude プロジェクトを、活動時刻に関係なく一覧化する。"""
import json
import os
import time
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
_HOME = Path(os.environ.get("OFFICE_HOME") or Path.home())
PROJECTS = _HOME / ".claude" / "projects"
_CACHE_SEC = 60.0
_cache = {"at": None, "data": None}
_cwd_cache = {}

# 掟: このデータ（ローカルパス一覧）を office_json に混ぜない＝中継に載せない。


def _nfc(value):
    return unicodedata.normalize("NFC", value or "")


def _load_config():
    data = Path(os.environ.get("OFFICE_DATA") or ROOT)
    path = Path(os.environ.get("OFFICE_CONFIG") or data / "office_config.json")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"projects": {}}
    if not isinstance(config, dict) or not isinstance(config.get("projects"), dict):
        return {"projects": {}}
    return config


def _inferred_cwd(dirname):
    return dirname.replace("-", "/")


def _resolve_cwd(dirname, latest):
    if dirname in _cwd_cache:
        return _cwd_cache[dirname]
    cwd = ""
    try:
        with latest.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= 50:
                    break
                try:
                    row = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                candidate = row.get("cwd") if isinstance(row, dict) else None
                if isinstance(candidate, str) and candidate:
                    cwd = candidate
                    break
    except OSError:
        pass
    cwd = cwd or _inferred_cwd(dirname)
    _cwd_cache[dirname] = cwd
    return cwd


def _project_meta(cwd, config):
    source = _nfc(cwd)
    for key, meta in config.get("projects", {}).items():
        if _nfc(key) in source and isinstance(meta, dict):
            return meta
    return {}


def _scan(now):
    config = _load_config()
    projects = []
    try:
        directories = list(PROJECTS.iterdir())
    except OSError:
        directories = []
    for directory in directories:
        try:
            if not directory.is_dir():
                continue
            sessions = []
            for path in directory.glob("*.jsonl"):
                try:
                    if path.is_file():
                        sessions.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        except OSError:
            continue
        if not sessions:
            continue
        last_active, latest = max(sessions, key=lambda item: item[0])
        cwd = _resolve_cwd(directory.name, latest)
        if not cwd:
            continue
        meta = _project_meta(cwd, config)
        name = meta.get("name") or Path(cwd).name
        age = now - last_active
        projects.append({
            "dir": directory.name,
            "cwd": cwd,
            "name": name,
            "lastActive": last_active,
            "ageSec": age,
            "sessions": len(sessions),
            "active": age < 6 * 3600,
        })
    projects.sort(key=lambda project: project["lastActive"], reverse=True)
    return {"generatedAt": now, "projects": projects}


def projects_json(now=None):
    """全 Claude プロジェクトを返す。結果は最大60秒キャッシュする。"""
    current = float(time.time() if now is None else now)
    cached_at = _cache["at"]
    if (_cache["data"] is not None and cached_at is not None
            and 0 <= current - cached_at < _CACHE_SEC):
        return _cache["data"]
    data = _scan(current)
    _cache["at"] = current
    _cache["data"] = data
    return data
