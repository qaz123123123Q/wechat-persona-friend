#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 export_chat.py 产出的 messages_*.json：数量/类型/语音/通话/拍一拍/逐日/接话速度/敏感词。"""

import argparse
import collections
import datetime
import json
import os
import re
import sys

FLAGS = {
    "钱/转账": r"转账|红包|借钱|还钱|付款|扫码|打钱|多少钱|学费|投资|理财|兼职|返利",
    "见面": r"见面|出来玩|一起吃饭|约|来找我|找你|在哪|过来",
    "感情": r"喜欢你|爱你|想你|男朋友|女朋友|对象|谈恋爱|在一起|表白|分手",
    "身份/联系": r"哪的|哪里人|几岁|多大|叫什么|名字|电话|手机号",
    "隐私/要钱": r"身份证|银行卡|验证码|密码|先给我|借点",
}


def ts(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


TYPES = {"图片", "语音", "视频", "表情贴纸", "位置", "位置共享", "通话", "系统", "拍一拍",
         "引用", "红包", "文件", "链接", "小程序", "合并转发", "卡片"}


def kind(text):
    m = re.match(r"\[([^\]]+)\]", text or "")
    if not m:
        return "文本"
    name = re.split(r"[ 「]", m.group(1))[0]
    if name.startswith("卡片") or name.startswith("类型"):
        return "卡片"
    return name if name in TYPES else "表情/其他"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="export_chat.py 产出的 messages_*.json")
    ap.add_argument("--out", default="", help="可选：把报告写到这个 markdown 文件")
    args = ap.parse_args()

    rows = json.load(open(args.json, encoding="utf-8"))
    rows.sort(key=lambda r: r["ts"])
    L = []

    def w(s=""):
        L.append(s)
        print(s)

    w("# 微信会话分析")
    w()
    w("区间：%s → %s ｜ 共 %d 条" % (ts(rows[0]["ts"]), ts(rows[-1]["ts"]), len(rows)))
    w()
    by = collections.Counter(r["side"] for r in rows)
    chars = collections.Counter()
    for r in rows:
        chars[r["side"]] += len(r["text"] or "")
    w("## 数量")
    for side in by:
        n = by[side]
        w("- %s：%d 条，%d 字，平均 %.1f 字/条" % (side, n, chars[side], chars[side] / n))

    w()
    w("## 内容类型")
    kc = collections.defaultdict(collections.Counter)
    for r in rows:
        kc[kind(r["text"])][r["side"]] += 1
    for k, c in sorted(kc.items(), key=lambda kv: -sum(kv[1].values())):
        w("- %s：%d %s" % (k, sum(c.values()), dict(c)))

    voice = collections.Counter()
    for r in rows:
        m = re.match(r"\[语音 (\d+)″\]", r["text"] or "")
        if m:
            voice[r["side"]] += int(m.group(1))
    if voice:
        w()
        w("## 语音：合计 %.1f 分钟 %s" % (sum(voice.values()) / 60, dict(voice)))

    calls = [r for r in rows if (r["text"] or "").startswith("[通话]")]
    if calls:
        w()
        w("## 通话（%d 条记录）" % len(calls))
        total = 0
        for r in calls:
            msg = r["text"][4:]
            m = re.fullmatch(r"通话时长 (\d+):(\d+)(?::(\d+))?", msg.strip())
            if m:
                a, b, c = m.groups()
                sec = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
                total += sec
            w("- %s %s %s" % (ts(r["ts"]), r["side"], msg))
        w("合计通话时长：%.1f 分钟（%.1f 小时）" % (total / 60, total / 3600))

    pats = collections.Counter(r["side"] for r in rows if (r["text"] or "").startswith("[拍一拍]"))
    if pats:
        w()
        w("## 拍一拍：%d 次 %s" % (sum(pats.values()), dict(pats)))

    w()
    w("## 逐日")
    days = collections.OrderedDict()
    for r in rows:
        days.setdefault(ts(r["ts"])[:10], []).append(r)
    for d, v in days.items():
        a = sum(1 for r in v if r["side"] == "我")
        b = len(v) - a
        span = (v[-1]["ts"] - v[0]["ts"]) / 3600
        w("- %s  %4d 条（我 %d / 对方 %d）  首条=%s %s  末条 %s  跨度 %0.1f 小时"
          % (d, len(v), a, b, v[0]["side"], ts(v[0]["ts"])[11:], ts(v[-1]["ts"])[11:], span))

    lat = collections.defaultdict(list)
    prev = None
    for r in rows:
        if prev and prev["side"] != r["side"] and r["ts"] - prev["ts"] < 6 * 3600:
            lat[r["side"]].append(r["ts"] - prev["ts"])
        prev = r
    w()
    w("## 接话间隔（中位数）")
    for side, v in lat.items():
        v.sort()
        w("- %s：中位 %.0f 秒，平均 %.0f 秒" % (side, v[len(v) // 2], sum(v) / len(v)))

    night = sum(1 for r in rows if 0 <= datetime.datetime.fromtimestamp(r["ts"]).hour < 6)
    w()
    w("## 时间分布")
    w("- 凌晨 0–6 点：%d 条（%.1f%%）" % (night, 100.0 * night / len(rows)))
    w("- 每天第一条发起者：" + str(dict(collections.Counter(v[0]["side"] for v in days.values()))))
    gaps = sorted(((rows[i]["ts"] - rows[i - 1]["ts"], i) for i in range(1, len(rows))), reverse=True)[:8]
    w("- 最长的 8 段沉默：")
    for g, i in gaps:
        w("    %5.1f 小时  %s %s → %s %s" % (g / 3600, ts(rows[i - 1]["ts"]), rows[i - 1]["side"],
                                             ts(rows[i]["ts"]), rows[i]["side"]))

    w()
    w("## 关键词命中")
    for name, rx in FLAGS.items():
        r = re.compile(rx)
        hit = [x for x in rows if r.search(x["text"] or "")]
        w("- %s：%d 条 %s" % (name, len(hit), dict(collections.Counter(x["side"] for x in hit))))
        for x in hit[:5]:
            w("    %s %s: %s" % (ts(x["ts"])[:16], x["side"], (x["text"] or "")[:60]))

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w", encoding="utf-8").write("\n".join(L))
        print("\n报告已写入:", args.out)


if __name__ == "__main__":
    main()
