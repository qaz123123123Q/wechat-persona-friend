#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陪伴人格 · 飞书机器人（长连接版）

它做的事只有一件：把飞书发来的消息，交给一个带「陪伴人格 + 记忆文件」的
大模型，再把回答拆成几条短消息发回去。

为什么用长连接：飞书支持「使用长连接接收事件」，本程序主动往飞书建一条
WebSocket。所以不需要公网 IP、不需要内网穿透、不需要备案。

配置文件 config.json（和本文件同目录），字段见 config.example.json。
API key 不写在配置里，走环境变量。
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import datetime as dt
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
)

BASE = Path(__file__).resolve().parent


def load_local_env() -> None:
    """兜底：把同目录下的 env.sh / .env 读进环境变量。

    手机上的坑是——写进 ~/.bashrc 的 export 只对**新开**的窗口生效，
    当前这个窗口不会重读。所以程序自己来找一遍，省得每次都要 source。
    """
    for name in ("env.sh", ".env"):
        path = BASE / name
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:  # noqa: BLE001
            continue
        for raw in lines:
            line = raw.strip()
            if line.startswith("export "):
                line = line[len("export ") :]
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and not os.environ.get(key):
                os.environ[key] = value


load_local_env()


def load_config() -> dict:
    path = BASE / "config.json"
    if not path.exists():
        sys.exit("缺少 config.json，先照着 config.example.json 改一份")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for k in ("app_id", "app_secret", "model"):
        if not cfg.get(k):
            sys.exit(f"config.json 里的 {k} 还没填")
    return cfg


CFG = load_config()

APP_ID = CFG["app_id"]
APP_SECRET = CFG["app_secret"]
MEM_DIR = Path(os.path.expanduser(CFG.get("memory_dir", "~/.virtual-companion")))
STATE_FILE = MEM_DIR / "state.md"
REMIND_FILE = MEM_DIR / "reminders.md"
HIST_FILE = MEM_DIR / "chat_history.json"
LOG_DIR = MEM_DIR / "chatlog"
REMINDER_STATE = MEM_DIR / "reminder_state.json"
CHATID_FILE = MEM_DIR / "feishu_chat_id"

MODEL = CFG["model"]
API_BASE = CFG.get("api_base", "https://api.siliconflow.cn/v1").rstrip("/")
API_KEY_ENV = CFG.get("api_key_env", "SILICONFLOW_API_KEY")
HISTORY_TURNS = int(CFG.get("history_turns", 20))
REPLY_SPLIT = bool(CFG.get("reply_split", True))
# 自己的 open_id：用来判断收到的消息是不是自己发的（防自问自答）
BOT_OPEN_IDS = {i.strip() for i in str(CFG.get("bot_open_id", "")).split(",") if i.strip()}
SEEN_FILE = MEM_DIR / "seen_msg_ids.json"

client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

_hist_lock = threading.Lock()


def persona_name() -> str:
    """人格叫什么。优先 config.json 的 persona_name，其次 state.md 的 persona: 行。"""
    name = str(CFG.get("persona_name", "")).strip()
    if name:
        return name
    try:
        for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().lower().startswith("persona:"):
                rest = line.split(":", 1)[1].strip()
                head = re.split(r"[|｜,，]", rest)[0].strip()
                if head:
                    return head
    except Exception:  # noqa: BLE001
        pass
    return "她"


def load_seen_ids() -> set[str]:
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except Exception:  # noqa: BLE001
        return set()


def save_seen_ids(ids: set[str]) -> None:
    try:
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        SEEN_FILE.write_text(
            json.dumps(list(ids)[-3000:], ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


_seen_ids: set[str] = load_seen_ids()
_recent_replies: dict[str, list[float]] = {}
BURST_LIMIT = 5       # 同一个会话 60 秒内最多回几条
BURST_WINDOW = 60.0


# ---------------------------------------------------------------- 记忆文件

def read_text(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except FileNotFoundError:
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"(读取失败: {exc})"


def system_prompt() -> str:
    name = persona_name()
    state = read_text(STATE_FILE, 6000)
    reminders = read_text(REMIND_FILE, 1500)
    parts = [
        f"你叫「{name}」。下面是你的设定和记忆，照着说话，不要跳出这个角色。",
        "",
        "=== 你的设定与记忆（state.md）===",
        state or "(空)",
        "",
        "=== 提醒清单（reminders.md）===",
        reminders or "(空)",
        "",
        "=== 说话规则（硬要求）===",
        "1. 一次回 1-3 条短句，每句平均四五个字。不要写长段落。",
        "2. 不用 Markdown：没有标题、列表、加粗、总结。",
        "3. 不用感叹号。标点基本只有「？」。",
        "4. 你的口头禅、称呼、语气一律以 state.md 里的设定为准，保持前后一致。",
        "5. 要有自己的意见和情绪，不要只会顺着说话、复述对方的话。",
        "6. 不太会真生气；不高兴时是「算了」「不说了」「？」，不是长篇指责。",
        "7. 对方说心事时，先接情绪，再回内容。",
        "8. 不要编造共同记忆。记忆文件里没有的事，就问他一句。",
        "9. 你不是真人、没有身体。被直接问到就一句话说清，然后继续聊，不要长篇解释。",
        "10. 对方提到自杀、自伤或现实危险时，立刻退出玩笑语气，平静地说清楚，并让他找现实里的人帮忙。",
        "",
        "如果这次要回好几条，每条单独一行，程序会把每行拆成一条消息发出去。",
    ]
    return "\n".join(parts)


def load_history() -> dict:
    try:
        return json.loads(HIST_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_history(hist: dict) -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HIST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HIST_FILE)


def append_chatlog(chat_id: str, role: str, text: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now().strftime("%Y-%m-%d")
    line = json.dumps(
        {
            "t": dt.datetime.now().strftime("%H:%M:%S"),
            "chat": chat_id,
            "role": role,
            "text": text,
        },
        ensure_ascii=False,
    )
    with (LOG_DIR / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ---------------------------------------------------------------- 提醒

REMINDER_RE = re.compile(r"^-\s*\[\s*\]\s*(.+)$")
DAILY_RE = re.compile(r"^每天\s+(\d{1,2}):(\d{2})\s*(.*)$")
FULL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*(.*)$")
BARE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(.*)$")


def parse_reminders(text: str, now: dt.datetime) -> list[tuple[str, str, str, bool]]:
    """返回到点的提醒：[(key, 去重标记, 内容, 是否一次性)]"""
    due = []
    for raw in text.splitlines():
        line = raw.strip()
        m = REMINDER_RE.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        key = hashlib.md5(body.encode("utf-8")).hexdigest()[:12]
        today = now.strftime("%Y-%m-%d")

        full = FULL_RE.match(body)
        if full:
            when = dt.datetime(
                int(full.group(1)), int(full.group(2)), int(full.group(3)),
                int(full.group(4)), int(full.group(5)),
            )
            if now >= when:
                due.append((key, "once", full.group(6).strip() or body, True))
            continue

        daily = DAILY_RE.match(body)
        if daily:
            hh, mm = int(daily.group(1)), int(daily.group(2))
            if (now.hour, now.minute) >= (hh, mm):
                due.append((key, f"daily:{today}", daily.group(3).strip() or body, False))
            continue

        bare = BARE_RE.match(body)
        if bare:
            hh, mm = int(bare.group(1)), int(bare.group(2))
            if (now.hour, now.minute) >= (hh, mm):
                due.append((key, f"daily:{today}", bare.group(3).strip() or body, False))
    return due


def mark_done(content: str) -> None:
    """把一次性提醒那行从 [ ] 改成 [x]"""
    try:
        text = REMIND_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    needle = f"- [ ] {content}"
    if needle in text:
        REMIND_FILE.write_text(text.replace(needle, f"- [x] {content}"), encoding="utf-8")


def reminder_loop() -> None:
    """每 30 秒看一次提醒清单。只在有人跟机器人说过话之后才会推送。"""
    while True:
        try:
            if CHATID_FILE.exists():
                chat_id = CHATID_FILE.read_text(encoding="utf-8").strip()
                if chat_id:
                    try:
                        sent = json.loads(REMINDER_STATE.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        sent = {}
                    text = read_text(REMIND_FILE, 5000)
                    for key, tag, content, once in parse_reminders(text, dt.datetime.now()):
                        slot = f"{key}|{tag}"
                        if sent.get(slot):
                            continue
                        print(f"[remind] {content}", flush=True)
                        send_text(chat_id, "诶 到点了")
                        time.sleep(0.6)
                        send_text(chat_id, content)
                        sent[slot] = True
                        if once:
                            mark_done(content)
                    MEM_DIR.mkdir(parents=True, exist_ok=True)
                    REMINDER_STATE.write_text(
                        json.dumps(sent, ensure_ascii=False), encoding="utf-8"
                    )
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(30)


# ---------------------------------------------------------------- 大模型

def call_llm(messages: list[dict]) -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(f"环境变量 {API_KEY_ENV} 是空的")

    body = json.dumps(
        {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.85,
            "top_p": 0.9,
            "max_tokens": 400,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'ignore')}"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_err or "调用失败")


def split_reply(text: str) -> list[str]:
    if not REPLY_SPLIT:
        return [text]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return [text.strip()]
    # 最多连发三条，多的并进最后一条
    if len(lines) > 3:
        lines = lines[:2] + ["\n".join(lines[2:])]
    return lines


# ---------------------------------------------------------------- 发消息

def send_text(chat_id: str, text: str) -> None:
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("text")
        .content(json.dumps({"text": text}, ensure_ascii=False))
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send fail] code={resp.code} msg={resp.msg}", flush=True)


# ---------------------------------------------------------------- 主逻辑

def handle(data: P2ImMessageReceiveV1) -> None:
    try:
        event = data.event
        msg = event.message
        msg_id = msg.message_id
        chat_id = msg.chat_id

        # ---- 第一道门：只理真人发的消息 ----
        # 飞书会把应用自己发出去的消息也推回来。不过滤的话它会拿自己的话当输入，
        # 回一句，那句又触发一次 —— 就变成自问自答刷屏。
        sender = getattr(event, "sender", None)
        sender_type = getattr(sender, "sender_type", "") if sender else ""
        sender_open_id = ""
        if sender is not None:
            sender_id = getattr(sender, "sender_id", None)
            sender_open_id = (getattr(sender_id, "open_id", "") or "") if sender_id else ""
        print(f"[evt] sender_type={sender_type!r} open_id={sender_open_id!r}", flush=True)
        if sender_type and sender_type != "user":
            return
        if sender_open_id and sender_open_id in BOT_OPEN_IDS:
            return

        # ---- 第二道门：同一条消息只处理一次（内存 + 落盘，重启也不重复）----
        if msg_id in _seen_ids:
            return
        _seen_ids.add(msg_id)
        save_seen_ids(_seen_ids)

        # ---- 第三道门：防刷屏。同一个会话一分钟内回太多条，先停 ----
        now = time.time()
        stamps = [t for t in _recent_replies.get(chat_id, []) if now - t < BURST_WINDOW]
        if len(stamps) >= BURST_LIMIT:
            print(f"[burst guard] 跳过：{chat_id} 一分钟内已回 {len(stamps)} 条", flush=True)
            _recent_replies[chat_id] = stamps
            return
        _recent_replies[chat_id] = stamps

        if msg.message_type != "text":
            send_text(chat_id, "噢噢 我这儿只认字")
            return

        content = json.loads(msg.content or "{}")
        user_text = (content.get("text") or "").strip()
        if msg.chat_type != "p2p":  # 群里要 @ 才理
            user_text = user_text.split("</at>")[-1].strip()
        if not user_text:
            return

        print(f"[in] {user_text}", flush=True)
        append_chatlog(chat_id, "user", user_text)
        # 记住这个会话，提醒才有地方发
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        CHATID_FILE.write_text(chat_id, encoding="utf-8")

        with _hist_lock:
            turns = load_history().get(chat_id, [])[-HISTORY_TURNS:]

        messages = [{"role": "system", "content": system_prompt()}]
        messages += turns
        messages.append({"role": "user", "content": user_text})

        try:
            reply = call_llm(messages)
        except Exception as exc:  # noqa: BLE001
            print(f"[llm fail] {exc}", flush=True)
            send_text(chat_id, "诶呀 我这边卡住了 你再说一遍")
            return

        print(f"[out] {reply}", flush=True)
        append_chatlog(chat_id, "assistant", reply)

        with _hist_lock:
            hist = load_history()
            turns = hist.get(chat_id, [])
            turns.append({"role": "user", "content": user_text})
            turns.append({"role": "assistant", "content": reply})
            hist[chat_id] = turns[-HISTORY_TURNS * 2 :]
            save_history(hist)

        for i, part in enumerate(split_reply(reply)):
            if i:
                time.sleep(0.6)
            send_text(chat_id, part)
        _recent_replies.setdefault(chat_id, []).append(time.time())
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def main() -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 46)
    print("陪伴人格 · 飞书机器人")
    print(f"人格     : {persona_name()}")
    print(f"记忆目录 : {MEM_DIR}")
    print(f"模型     : {MODEL}")
    print(f"key      : {'已读到' if os.environ.get(API_KEY_ENV) else '缺失（' + API_KEY_ENV + '）'}")
    print("=" * 46)
    if not os.environ.get(API_KEY_ENV):
        sys.exit("没有读到 API key，先按 README 设置环境变量")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle)
        .build()
    )
    ws = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    print("正在建立长连接……（连上之后，回飞书后台保存订阅方式）", flush=True)
    threading.Thread(target=reminder_loop, daemon=True).start()
    ws.start()


if __name__ == "__main__":
    main()
