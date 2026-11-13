#!/usr/bin/env python3
"""整理下载目录：按扩展名归类到子目录。JARVIS_PARAMS 传入 keep_days/dry_run。"""
import json
import os
import shutil
import time
from pathlib import Path

params = json.loads(os.environ.get("JARVIS_PARAMS", "{}"))
keep_days = int(params.get("keep_days", 7))
dry_run = bool(params.get("dry_run", True))

DOWNLOADS = Path.home() / "Downloads"
CATEGORIES = {
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"},
    "Archives": {".zip", ".tar", ".gz", ".dmg", ".pkg", ".rar", ".7z"},
    "Video": {".mp4", ".mov", ".avi", ".mkv"},
    "Audio": {".mp3", ".wav", ".m4a", ".aac"},
}

cutoff = time.time() - keep_days * 86400
moved, skipped = 0, 0
for f in DOWNLOADS.iterdir():
    if f.name.startswith(".") or f.is_dir():
        continue
    if f.stat().st_mtime >= cutoff:
        skipped += 1
        continue
    cat = next((c for c, exts in CATEGORIES.items() if f.suffix.lower() in exts), "Others")
    dest_dir = DOWNLOADS / cat
    dest_dir.mkdir(exist_ok=True)
    print(f"{'[dry-run] ' if dry_run else ''}{f.name} -> {cat}/")
    if not dry_run:
        shutil.move(str(f), str(dest_dir / f.name))
    moved += 1

mode = "演练（dry_run）" if dry_run else "实际执行"
print(f"\n{mode}: 移动 {moved} 个文件，保留最近 {keep_days} 天内 {skipped} 个文件")
