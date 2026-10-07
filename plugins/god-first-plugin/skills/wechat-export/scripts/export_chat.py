#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出微信 4.x 的任意会话（单聊或群聊），包含被 zstd 压缩的消息内容。

用法：
  python export_chat.py --db-root <解密后的根目录> --identifier <微信号/wxid/昵称/备注片段> \
      --out-dir <输出目录> [--me wxid_...] [--label 名字] [--sevenzip <7z.exe>]
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

SHARDS = ("message_0.db", "message_1.db")
SUB49 = {5: "链接", 6: "文件", 17: "位置共享", 19: "合并转发", 33: "小程序",
         57: "引用", 62: "拍一拍", 2001: "红包"}
PLAIN = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情贴纸]", 48: "[位置]", 10000: "[系统]"}


def ts(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def pick(pattern, s, default=""):
    m = re.search(pattern, s or "", re.S)
    return m.group(1).strip() if m else default


def infer_me(db_root, data_root):
    for p in (data_root, db_root):
        if not p:
            continue
        for part in reversed([x for x in re.split(r"[\\/]+", p) if x]):
            m = re.match(r"^(wxid_[0-9A-Za-z]+)_", part)
            if m:
                return m.group(1)
    return None


def find_contacts(contact_db, ident):
    con = sqlite3.connect(contact_db)
    cur = con.cursor()
    like = "%" + ident + "%"
    hits, seen = [], set()
    for col in ("alias", "username", "nick_name", "remark"):
        for r in cur.execute(
            "select username, alias, nick_name, remark from contact where [%s] like ?" % col, (like,)
        ):
            if r[0] and r[0] not in seen:
                seen.add(r[0])
                hits.append(dict(zip(("username", "alias", "nick_name", "remark"), r)))
    con.close()
    return hits


def decompress_all(blobs, sevenzip, tmp_root):
    """blobs: {key: bytes(zstd frame)} -> {key: text}"""
    if not blobs:
        return {}
    d_in = os.path.join(tmp_root, "zst")
    d_out = os.path.join(tmp_root, "zst_out")
    os.makedirs(d_in, exist_ok=True)
    os.makedirs(d_out, exist_ok=True)
    for key, data in blobs.items():
        with open(os.path.join(d_in, key + ".zst"), "wb") as fh:
            fh.write(data)
    subprocess.run([sevenzip, "x", "-y", "-o" + d_out, os.path.join(d_in, "*.zst")],
                   capture_output=True)
    out = {}
    for key in blobs:
        p = os.path.join(d_out, key)
        if os.path.exists(p):
            out[key] = open(p, "rb").read().decode("utf-8", "replace")
    return out


def render(ltype, text):
    b, s = ltype & 0xFFFFFFFF, ltype >> 32
    t = text or ""
    if b == 1:
        return t.replace("\r\n", " ").replace("\n", " ").strip() or "[空]"
    if b == 34:
        m = re.search(r'voicelength="(\d+)"', t)
        return "[语音 %s″]" % round(int(m.group(1)) / 1000) if m else "[语音]"
    if b == 43:
        m = re.search(r'playlength="(\d+)"', t)
        return "[视频 %s″]" % m.group(1) if m else "[视频]"
    if b == 48:
        return "[位置] " + pick(r'label="([^"]*)"', t)
    if b == 50:
        return "[通话] " + pick(r"<!\[CDATA\[(.*?)\]\]>", t, "通话")
    if b == 10000:
        if "撤回" in t:
            # revokemsg 是 <content>…</content>；这里以前写成 content">… 导致渲染为空
            return "[系统] " + (pick(r"<content>(.*?)</content>", t) or pick(r'content">(.*?)<', t))
        return "[系统] " + t.strip()
    if b == 49:
        if s == 62:
            return "[拍一拍] " + pick(r"<title>(.*?)</title>", t)
        if s == 57:
            return "[引用 「%s」] %s" % (pick(r"<content>(.*?)</content>", t),
                                        pick(r"<title>(.*?)</title>", t))
        if s == 2001:
            return "[红包] " + pick(r"<des><!\[CDATA\[(.*?)\]\]></des>", t)
        if s == 6:
            return "[文件] " + pick(r"<title>(.*?)</title>", t)
        if s == 5:
            return "[链接] %s %s" % (pick(r"<title>(.*?)</title>", t), pick(r"<url>(.*?)</url>", t))
        if s == 33:
            return "[小程序] " + pick(r"<title>(.*?)</title>", t)
        if s == 19:
            return "[合并转发] " + pick(r"<title>(.*?)</title>", t)
        if s == 17:
            return "[位置共享] " + pick(r"<!\[CDATA\[(.*?)\]\]>", t)
        return "[卡片%d]" % s
    return PLAIN.get(b, "[类型%d%s]" % (b, "/%d" % s if s else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-root", required=True, help="解密后的根目录（里面有 contact\\ 和 message\\）")
    ap.add_argument("--data-root", default="", help="原始 db_storage（用来推断机主 wxid）")
    ap.add_argument("--identifier", required=True, help="微信号 / wxid / 昵称 / 备注 的任意片段")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--me", default="", help="机主自己的 wxid（留空则从路径推断）")
    ap.add_argument("--label", default="", help="输出文件名用的标签")
    ap.add_argument("--sevenzip", default=r"C:\Program Files\7-Zip\7z.exe")
    args = ap.parse_args()

    db_root = os.path.abspath(args.db_root)
    contact_db = os.path.join(db_root, "contact", "contact.db")
    if not os.path.exists(contact_db):
        sys.exit("找不到 contact.db：%s" % contact_db)

    hits = find_contacts(contact_db, args.identifier)
    if not hits:
        sys.exit("没有联系人匹配 %r。可用微信号 / wxid / 昵称 / 备注的任意片段。" % args.identifier)
    if len(hits) > 1:
        print("%d 个候选，请用更精确的关键词重试：" % len(hits))
        for h in hits[:40]:
            print("   ", h)
        sys.exit(1)
    hit = hits[0]
    wxid = hit["username"]
    label = args.label or hit["remark"] or hit["nick_name"] or hit["alias"] or wxid
    me = args.me or infer_me(db_root, args.data_root) or ""
    print("匹配到:", hit)
    print("机主 wxid:", me or "(未推断出来，将把所有非对方消息标成 [发送者])")

    rows, zsts = [], {}
    for shard in SHARDS:
        path = os.path.join(db_root, "message", shard)
        if not os.path.exists(path):
            continue
        con = sqlite3.connect(path)
        cur = con.cursor()
        table = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()
        if not cur.execute("select count(*) from sqlite_master where name=?", (table,)).fetchone()[0]:
            con.close()
            continue
        n2i = {r[0]: r[1] for r in cur.execute("select rowid, user_name from Name2Id")}
        for lid, ltype, sender, ctime, content, ct in cur.execute(
            "select local_id, local_type, real_sender_id, create_time, message_content, "
            "WCDB_CT_message_content from [%s]" % table
        ):
            who = n2i.get(sender)
            key = "%s_%s" % (shard.replace(".db", ""), lid)
            if ct:
                zsts[key] = content
            rows.append(dict(key=key, who=who, ltype=ltype, ts=ctime, raw=content))
        con.close()

    if not rows:
        sys.exit("这个会话在两个分片里都没有消息（会话表不存在或已清空）。")

    decoded = {}
    if zsts:
        tmp = tempfile.mkdtemp(prefix="wxzstd_")
        print("用 7-Zip 解压 %d 条压缩消息..." % len(zsts))
        decoded = decompress_all(zsts, args.sevenzip, tmp)
        shutil.rmtree(tmp, ignore_errors=True)

    out = []
    for r in rows:
        text = decoded.get(r["key"])
        if text is None:
            raw = r["raw"]
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
        if me and r["who"] == me:
            side = "我"
        elif r["who"] == wxid:
            side = "对方"
        elif not r["who"]:
            side = "系统"          # 系统消息在 Name2Id 里查不到发送者（空串或 None）
        else:
            side = "[%s]" % r["who"]
        out.append(dict(ts=r["ts"], side=side, who=r["who"], ltype=r["ltype"],
                        text=render(r["ltype"], text)))
    out.sort(key=lambda r: r["ts"])

    os.makedirs(args.out_dir, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", label)
    txt = os.path.join(args.out_dir, "微信聊天记录_%s_%s至%s.txt"
                       % (safe, ts(out[0]["ts"])[:10], ts(out[-1]["ts"])[:10]))
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("# 会话：%s（%s）\n# 机主：%s ｜ 共 %d 条 ｜ %s 至 %s\n\n"
                 % (label, wxid, me or "?", len(out), ts(out[0]["ts"]), ts(out[-1]["ts"])))
        for r in out:
            fh.write("[%s] %s: %s\n" % (ts(r["ts"]), r["side"], r["text"]))
    js = os.path.join(args.out_dir, "messages_%s.json" % safe)
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)

    print("\n全文：", txt)
    print("结构化：", js)
    print("总条数：", len(out), " 时间范围：", ts(out[0]["ts"]), "->", ts(out[-1]["ts"]))
    by = collections.Counter(r["side"] for r in out)
    print("按发送方：", dict(by))
    known = {"图片", "语音", "视频", "表情贴纸", "位置", "位置共享", "通话", "系统", "拍一拍",
             "引用", "红包", "文件", "链接", "小程序", "合并转发"}

    def _kind(text):
        m = re.match(r"\[([^\]]+)\]", text or "")
        if not m:
            return "文本"
        name = re.split(r"[ 「]", m.group(1))[0]
        return name if name in known else "表情/其他"

    kind = collections.Counter(_kind(r["text"]) for r in out)
    print("按类型：", kind.most_common(12))
    months = collections.Counter(ts(r["ts"])[:7] for r in out)
    print("按月份：")
    for k in sorted(months):
        a = sum(1 for r in out if ts(r["ts"])[:7] == k and r["side"] == "我")
        b = sum(1 for r in out if ts(r["ts"])[:7] == k and r["side"] == "对方")
        print("   %s  %6d  (我 %d / 对方 %d)" % (k, months[k], a, b))
    days = collections.OrderedDict()
    for r in out:
        days.setdefault(ts(r["ts"])[:10], []).append(r)
    print("每天首条发起者：", dict(collections.Counter(v[0]["side"] for v in days.values())))
    print("最忙的 10 天：", collections.Counter({d: len(v) for d, v in days.items()}).most_common(10))


if __name__ == "__main__":
    main()
