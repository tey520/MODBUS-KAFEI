from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil

from .models import Project


def load_project(path: str | Path) -> Project:
    project_path = Path(path)
    with project_path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("專案根節點必須是物件")
    project = Project.from_dict(data)
    errors = project.validate()
    if errors:
        raise ValueError("專案驗證失敗:\n" + "\n".join(errors[:20]))
    return project


def save_project(project: Project, path: str | Path, *, keep_backup: bool = True) -> Path:
    errors = project.validate()
    if errors:
        raise ValueError("保存前驗證失敗:\n" + "\n".join(errors[:20]))
    project_path = Path(path)
    if project_path.suffix.lower() != ".kafei":
        project_path = project_path.with_suffix(".kafei")
    project_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = project_path.with_suffix(project_path.suffix + ".tmp")
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if keep_backup and project_path.exists():
            backup_dir = project_path.parent / ".kafei-backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(project_path, backup_dir / f"{project_path.stem}-{stamp}.kafei")
            _trim_backups(backup_dir, project_path.stem, keep=10)
        os.replace(temporary, project_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return project_path


def save_autosave(project: Project, project_path: str | Path) -> Path:
    errors = project.validate()
    if errors:
        raise ValueError("自動保存前驗證失敗:\n" + "\n".join(errors[:20]))
    target = Path(str(project_path) + ".autosave")
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def recoverable_autosave(project_path: str | Path) -> Path | None:
    source = Path(project_path)
    autosave = Path(str(source) + ".autosave")
    if autosave.exists() and (not source.exists() or autosave.stat().st_mtime > source.stat().st_mtime):
        return autosave
    return None


def _trim_backups(directory: Path, stem: str, keep: int) -> None:
    backups = sorted(directory.glob(f"{stem}-*.kafei"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink()
