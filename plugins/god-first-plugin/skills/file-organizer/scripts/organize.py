#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按规则整理文件夹：默认干跑，--apply 才移动，--undo 可回滚。永不删除文件。"""

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import sys

DEFAULT_RULES = {
    "jpg": "图片", "jpeg": "图片", "png": "图片", "gif": "图片", "webp": "图片", "bmp": "图片",
    "heic": "图片", "svg": "图片", "raw": "图片",
    "mp4": "视频", "mov": "视频", "avi": "视频", "mkv": "视频", "flv": "视频", "wmv": "视频",
    "mp3": "音频", "wav": "音频", "flac": "音频", "m4a": "音频", "aac": "音频", "ogg": "音频",
    "pdf": "文档", "doc": "文档", "docx": "文档", "txt": "文档", "md": "文档", "rtf": "文档",
    "xls": "表格", "xlsx": "表格", "csv": "表格", "tsv": "表格",
    "ppt": "演示", "pptx": "演示", "key": "演示",
    "zip": "压缩包", "rar": "压缩包", "7z": "压缩包", "tar": "压缩包", "gz": "压缩包",
    "exe": "安装包", "msi": "安装包", "apk": "安装包", "dmg": "安装包",
    "py": "代码", "js": "代码", "ts": "代码", "java": "代码", "c": "代码", "cpp": "代码",
    "cs": "代码", "go": "代码", "rs": "代码", "sh": "代码", "ps1": "代码", "sql": "代码",
    "html": "代码", "css": "代码", "json": "代码", "yaml": "代码", "yml": "代码", "toml": "代码",
    "epub": "电子书", "mobi": "电子书", "azw3": "电子书",
    "ttf": "字体", "otf": "字体", "woff": "字体", "woff2": "字体",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".codex", ".venv", "venv", "AppData",
             "Windows", "$RECYCLE.BIN", "System Volume Information", ".idea", ".vscode"}


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def load_rules(path):
    if not path:
        return dict(DEFAULT_RULES)
    rules = dict(DEFAULT_RULES)
    rules.update(json.load(open(path, encoding="utf-8")))
    return {k.lower().lstrip("."): v for k, v in rules.items()}


def collect(root, rules, by, min_age_minutes, include_hidden):
    now = datetime.datetime.now().timestamp()
    cutoff = min_age_minutes * 60
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and (include_hidden or not d.startswith("."))]
        for fn in filenames:
            if not include_hidden and fn.startswith("."):
                continue
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if now - st.st_mtime < cutoff:
                continue
            if by == "date":
                cat = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m")
            elif by == "project":
                token = re.split(r"[-_\s.]+", fn)[0][:20] or "未分类"
                cat = token
            else:
                ext = os.path.splitext(fn)[1].lstrip(".").lower()
                cat = rules.get(ext, "其他")
            items.append((p, cat, st.st_size))
    return items


def unique_target(dst_dir, filename):
    target = os.path.join(dst_dir, filename)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(filename)
    i = 1
    while True:
        cand = os.path.join(dst_dir, "%s-%d%s" % (stem, i, ext))
        if not os.path.exists(cand):
            return cand
        i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="要整理的根目录")
    ap.add_argument("--dest", default="", help="归类到哪（默认就在 root 下建分类目录）")
    ap.add_argument("--by", choices=["type", "date", "project"], default="type")
    ap.add_argument("--rules", default="", help="自定义规则 JSON")
    ap.add_argument("--apply", action="store_true", help="真的移动（不加就是干跑）")
    ap.add_argument("--undo", default="", help="按日志回滚")
    ap.add_argument("--min-age-minutes", type=int, default=10)
    ap.add_argument("--include-hidden", action="store_true")
    args = ap.parse_args()

    if args.undo:
        log = json.load(open(args.undo, encoding="utf-8"))
        done = 0
        for rec in reversed(log["moves"]):
            if os.path.exists(rec["to"]):
                os.makedirs(os.path.dirname(rec["from"]), exist_ok=True)
                shutil.move(rec["to"], rec["from"])
                done += 1
        print("已回滚 %d 个文件" % done)
        return

    if not args.root:
        sys.exit("要指定 --root（干跑也一样）")
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        sys.exit("不是有效目录：%s" % root)

    dest_root = os.path.abspath(args.dest) if args.dest else root
    items = collect(root, load_rules(args.rules), args.by, args.min_age_minutes, args.include_hidden)
    if not items:
        print("没有需要整理的文件（都被跳过规则排除了：太新、隐藏、或在保护目录里）")
        return

    groups = collections.Counter()
    sizes = collections.Counter()
    for p, cat, sz in items:
        groups[cat] += 1
        sizes[cat] += sz

    print("根目录：%s" % root)
    print("分类方式：%s ｜ 待整理：%d 个文件，共 %s" % (args.by, len(items), human(sum(sizes.values()))))
    print()
    print("| 分类 | 文件数 | 大小 |")
    print("|---|---|---|")
    for cat, n in groups.most_common():
        print("| %s | %d | %s |" % (cat, n, human(sizes[cat])))
    print()
    print("明细（前 20 条）：")
    for p, cat, sz in items[:20]:
        print("  %s  →  %s/" % (os.path.relpath(p, root), cat))
    if len(items) > 20:
        print("  …还有 %d 条" % (len(items) - 20))

    if not args.apply:
        print()
        print("== 这是干跑，什么都没动。确认后加 --apply 才会移动。 ==")
        return

    log_path = os.path.join(dest_root, "organize_log_%s.json"
                            % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    moves = []
    for p, cat, sz in items:
        dst_dir = os.path.join(dest_root, cat)
        os.makedirs(dst_dir, exist_ok=True)
        target = unique_target(dst_dir, os.path.basename(p))
        shutil.move(p, target)
        moves.append({"from": p, "to": target})
    json.dump({"root": root, "time": datetime.datetime.now().isoformat(),
               "moves": moves}, open(log_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print()
    print("已移动 %d 个文件。" % len(moves))
    print("回滚日志：%s" % log_path)
    print("要撤回就运行：python organize.py --undo '%s'" % log_path)


if __name__ == "__main__":
    main()
