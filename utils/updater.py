from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from utils.brand import GITHUB_BRANCH, GITHUB_REPO, VERSION
from utils.config import ROOT

CACHE = ROOT / "storage" / "cache" / "update.json"
ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"
COMMIT_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
RAW_MAIN_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/main.py"
UA = {"User-Agent": "StarvellDxvxlz-Updater", "Accept": "application/vnd.github+json"}

KEEP_PREFIXES = (
    "configs/_main.cfg",
    "configs/auto_delivery.cfg",
    "configs/auto_response.cfg",
    "storage/",
    "logs/",
    "plugins/",
    ".git/",
    ".venv/",
    "venv/",
)
SKIP_NAMES = {"GITHUB.txt"}


@dataclass
class UpdateInfo:
    ok: bool
    has_update: bool
    local_version: str
    remote_version: str
    local_sha: str
    remote_sha: str
    error: str = ""


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _git_head() -> str:
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        return ""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return (out or "").strip()
    except Exception:
        return ""


def _http_get(url: str, timeout: float = 20.0) -> httpx.Response:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=UA) as client:
        response = client.get(url)
        response.raise_for_status()
        return response


def _remote_sha() -> str:
    data = _http_get(COMMIT_URL, timeout=10.0).json()
    return str(data.get("sha") or "")


def _parse_version(text: str) -> str:
    match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else ""


def _remote_version() -> str:
    urls = (
        f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/utils/brand.py",
        RAW_MAIN_URL,
    )
    for url in urls:
        try:
            ver = _parse_version(_http_get(url, timeout=10.0).text)
            if ver:
                return ver
        except Exception:
            continue
    return ""


def check_update() -> UpdateInfo:
    local_sha = str(_load_cache().get("sha") or _git_head() or "")
    try:
        remote_sha = _remote_sha()
        remote_version = _remote_version() or "?"
    except Exception as exc:
        return UpdateInfo(False, False, VERSION, "?", local_sha, "", str(exc))
    if not remote_sha:
        return UpdateInfo(False, False, VERSION, remote_version, local_sha, "", "GitHub не вернул коммит")
    if local_sha:
        has_update = not remote_sha.startswith(local_sha) and not local_sha.startswith(remote_sha)
    else:
        has_update = bool(remote_version and remote_version != VERSION)
    return UpdateInfo(True, has_update, VERSION, remote_version, local_sha, remote_sha)


def _rel_from_zip(name: str) -> str | None:
    parts = Path(name).parts
    if not parts:
        return None
    rel = str(Path(*parts[1:])) if len(parts) > 1 else ""
    rel = rel.replace("\\", "/")
    if not rel or rel.endswith("/"):
        return None
    if ".." in Path(rel).parts:
        return None
    return rel


def _skip(rel: str) -> bool:
    if rel in SKIP_NAMES or Path(rel).name in SKIP_NAMES:
        return True
    for prefix in KEEP_PREFIXES:
        clean = prefix.rstrip("/")
        if rel == clean or rel.startswith(prefix) or rel.startswith(clean + "/"):
            return True
    return False


def run_update() -> str:
    info = check_update()
    if not info.ok:
        return f"Не удалось связаться с GitHub: {info.error}"
    try:
        payload = _http_get(ZIP_URL, timeout=60.0).content
    except Exception as exc:
        return f"Не удалось скачать архив: {exc}"
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except Exception as exc:
        return f"Архив с GitHub битый: {exc}"
    written = 0
    for item in zf.infolist():
        if item.is_dir():
            continue
        rel = _rel_from_zip(item.filename)
        if not rel or _skip(rel):
            continue
        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(item) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        written += 1
    _save_cache({"sha": info.remote_sha, "version": info.remote_version or VERSION})
    pip_note = _install_requirements()
    extra = f"\n{pip_note}" if pip_note else ""
    return (
        f"Обновление установлено: {written} файлов.\n"
        f"Версия на GitHub: {info.remote_version or '?'}\n"
        f"Перезапусти start.bat.{extra}"
    )


def _install_requirements() -> str:
    req = ROOT / "requirements.txt"
    if not req.exists():
        return ""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
            cwd=str(ROOT),
        )
        return "Зависимости из requirements.txt обновлены."
    except Exception as exc:
        return f"pip install не вышел: {exc}. Запусти setup.bat."
