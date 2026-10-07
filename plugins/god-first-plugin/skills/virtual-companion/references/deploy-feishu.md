# 把陪伴人格挂到手机上的飞书（长连接方案）

目标：人格住在手机上，打开飞书就能跟她说话，**电脑可以关**。

这份文档整理自一次真实的部署（2026-10-08 凌晨，从零到跑通约 1 小时），包含四个真实踩过的坑。照着走能一次通。

---

## 0. 先想清楚整条链路

这不是一个孤立的机器人。完整链路有三步，**后一步依赖前一步的产物**：

```text
① wechat-export        导出本机微信聊天记录
        |
        v
② virtual-companion    从记录里提炼"她怎么说话"，沉淀成 state.md
        |
        v
③ 本文档                把 state.md + 一个大模型，挂到飞书长连接上
                        -> 手机上随时能聊，记忆和电脑上那份是同一份
```

所以：**只做第 ③ 步，你会得到一个没有记忆的普通聊天机器人。** 人格从哪来，取决于第 ①② 步有没有认真做。

---

## 1. 为什么是飞书（而不是微信 / 网页 / 云服务器）

已核验的事实：飞书开放平台支持**「使用长连接接收回调」**——你的程序主动往飞书建一条 WebSocket，事件从飞书推过来。这带来四个直接好处：

| 好处 | 说明 |
|---|---|
| 不需要公网 IP | 你是往外连，不是别人连你 |
| 不需要内网穿透 | 同理，ngrok / frp 这类东西全省 |
| 不需要备案 | 没有"对外提供网站"这个动作 |
| 不需要买服务器 | 自建应用 + 长连接免费 |

对比另外两条路：

- **微信个人号机器人**：微信没有给个人号开任何官方接口。社区那批项目（UI 自动化 / hook / 第三方协议）**全部违反微信服务协议**，轻则限制登录，重则封号。不值得拿主号赌。
- **局域网网页版**：二十分钟能做出来，零风险，但只能在家里同一个 WiFi 下用。适合"先跑起来试试"，不适合随身。

**结论**：要"手机上随时能聊"又不想违规，飞书是目前最干净的一条。

---

## 2. 你要做的部分：飞书后台（约 15 分钟）

1. 手机和电脑都装飞书，用手机号注册一个**个人账号**，顺手建一个团队（免费，不需要公司资质）。
2. 电脑浏览器打开 <https://open.feishu.cn> → 开发者后台 → 创建**「企业自建应用」**，名字随便填。
3. 「凭证与基础信息」里复制两串东西：**App ID** 和 **App Secret**。填进后面的 `config.json`。
4. 「权限管理」开两条：`im:message`（获取与发送单聊、群组消息）、`im:message:send_as_bot`（以应用身份发消息）。
5. 「应用能力」里添加**机器人**。
6. 「事件与回调」→ 订阅方式选**「使用长连接接收回调」**。注意：**保存这一步时，程序必须已经在跑**，否则长连接校验会失败——这是所有教程里翻车最多的一步，先启动 `bot.py`，看到"正在建立长连接"，再回来点保存。
7. 同一页「添加事件」→ 搜"接收消息"→ 勾选**接收消息 v2.0（`im.message.receive_v1`）**。
8. 创建版本并发布。你就是这个团队的管理员，自己批自己，秒过。

---

## 3. 你要做的部分：装到手机上（Termux）

载体随便一台**能一直开着的 Android 设备**——旧手机最合适。iOS 不行（没有 Termux）。

```bash
# 1) 装基础环境。Termux 请从 F-Droid 或 GitHub 装，不要用应用商店那个（商店版停更，包管理是坏的）
pkg install -y python

# 2) 让系统别杀它
termux-wake-lock
#    再去 设置 → 应用 → Termux → 电池 → 改成「无限制 / 不优化」

# 3) 把 companion-bot 目录拷到手机（adb、网盘、git clone 都行）
cd ~/companion-bot

# 4) 装依赖
python -m pip install -r requirements.txt

# 5) 填配置
cp config.example.json config.json
#    编辑 config.json，填 app_id / app_secret / persona_name

# 6) 存 API key
echo 'export SILICONFLOW_API_KEY=sk-你的key' >> ~/.bashrc
echo 'export SILICONFLOW_API_KEY=sk-你的key' >> ~/.bash_profile   # Termux 开机读的是这个

# 7) 跑起来
python bot.py
```

看到这两行就是活了：

```text
key : 已读到
正在建立长连接……
[Lark] ... connected to wss://msg-frontier.feishu.cn/ws/v2
```

`install-termux.sh` 把 1–6 步合成了一条命令，可以直接 `sh install-termux.sh`。

---

## 4. 记忆文件

程序读 `~/.virtual-companion/`（注意开头有个点，是隐藏目录）：

| 文件 | 作用 | 谁写 |
|---|---|---|
| `state.md` | 稳定事实、偏好、没完的事 | 你（或第 ② 步生成） |
| `reminders.md` | 提醒清单 | 你 / 对话中追加 |
| `chat_history.json` | 最近若干轮对话 | 程序自动 |
| `chatlog/YYYY-MM-DD.jsonl` | 全部聊天流水 | 程序自动 |

把电脑上那份 `state.md` / `reminders.md` 拷进去，她就记得你是谁。

### 提醒是怎么响的

程序每 30 秒扫一次 `reminders.md`，三种写法都认：

```markdown
- [ ] 2026-10-08 21:00 上床睡觉      # 一次性，发完自动改成 [x]
- [ ] 每天 22:30 提醒睡觉            # 每天
- [ ] 09:00 交英语作业               # 当天这个点
```

改完文件**不用重启**，下一轮扫描就生效。注意：只有在有人跟机器人说过话之后才会推送（它得先知道该往哪个会话发）。

---

## 5. 开机自启（可选，但强烈建议）

需要额外装 **Termux:Boot**（F-Droid 上那个插件 App，装完打开一次）。

```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-bot.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
. $HOME/.bashrc
cd $HOME/companion-bot && python bot.py >> $HOME/companion-bot/bot.log 2>&1 &
EOF
chmod +x ~/.termux/boot/start-bot.sh
```

---

## 6. 四个真实的坑（都在这份代码里修好了）

### 坑 1：保存"长连接"时程序没在跑

飞书点保存那一刻会做一次连接校验。程序没在跑 → 校验失败 → 你以为是自己填错了。顺序：**先跑程序，再点保存。**

### 坑 2：Termux 的 `pkg update` 是坏的

报错通常长这样：

```text
CANNOT LINK EXECUTABLE "curl": cannot locate symbol "SSL_set0_rbio" ...
```

原因是 Termux 的 `pkg` 内部调用 `curl`，而 `curl` 和 `openssl` 版本对不上。**别去修 curl**——你自己的 Python 是好的，`pip` 走的是 Python 自带的加密库，跟 curl 无关。直接跳过 `pkg update`：

```bash
apt install -y python
python -m pip install -r requirements.txt
```

`install-termux.sh` 里已经去掉了 `pkg update`（只检查、只补装，而且用 `python -m pip` 避免 PATH 不生效）。

### 坑 3：`export` 写进 `~/.bashrc`，当前窗口读不到

`~/.bashrc` 只在**新开**的窗口生效。你刚写完 key，当前这个窗口里跑 `python bot.py` 依然会说"没读到 key"。补救：

```bash
. ~/companion-bot/env.sh              # 当前窗口立刻生效
```

另外 Termux 开机读的其实是 `~/.bash_profile`，不是 `~/.bashrc`，所以两处都写一遍。**本代码还做了一层兜底**：`bot.py` 启动时会自己去读同目录下的 `env.sh` / `.env`，就算你忘了 `source` 也能跑。

### 坑 4：机器人自己回自己（最凶的一个）

飞书会把**应用自己发出去的消息**也当成事件推回来。不过滤的话：

```text
它回你一句 -> 那句被当成新消息喂给它 -> 它接着回 -> 无穷
```

表现是"你只发了一条『早安』，它回了八条"，而且读起来像**有人在自言自语**——因为它后半段确实在跟自己聊。

代码里上了四道门：

1. `sender_type != "user"` 直接丢（机器人自己发的）
2. `message_id` 去重（内存 + 落盘，重启也不重复）
3. 同一会话 60 秒内最多回 5 条（限速，**注意它只是减速，不能止住循环**）
4. **回声熔断**：记下自己最近 2 分钟发过的话；进来的消息跟刚说过的一字不差 → 判定是回声，直接丢

> ⚠️ 第 1 道门有个**容易写错的坑**：写成 `if sender_type and sender_type != "user": return`
> 是 **fail-open** 的——飞书没给 `sender_type` 时它会**放行**，门等于没关。所以必须再压一道
> 不依赖飞书字段的第 4 道门兜底。另外 `config.json` 里的 `bot_open_id` 一定要填真值
> （用 `/open-apis/bot/v3/info` 查），没配的话第 2 道门也形同虚设。

**修复后要清一次历史**，否则它还会顺着刚才那股劲儿胡言乱语：

```bash
rm -f ~/.virtual-companion/chat_history.json
```

（只丢短期上下文，`state.md` 和提醒清单不动。）

---

## 7. 成本与风险

### 钱

- 飞书：**0 元**。自建应用、长连接、收发消息全免费。
- 模型：按 token 计费。每轮把「人格 + state.md + 最近 N 轮历史」重新发一遍，大约 2000–4000 输入 token，量级是**每轮好几厘到一分钱**（具体单价以你用的模型为准）。
- 想省钱三个旋钮：`history_turns` 调小、换更便宜的模型、系统提示词不全量带 `state.md`。

### 代价（躲不掉的三条）

1. 手机得一直开着、Termux 不能被杀后台。重启 / 断网 / 被系统清掉 = 下线。
2. 聊天内容会经过**飞书服务器**，再过**模型厂商服务器**。
3. App Secret 和 API key 明文躺在手机里。手机丢了或借出去 = 别人能花你的钱。

### 安全清单

- 密钥文件、`config.json`、`env.sh` 不要进任何 git 仓库（插件根目录 `.gitignore` 已排除）。
- `config.json` 里只放 App ID / Secret；**API key 走环境变量，不写进配置**。
- 别把安装命令粘到聊天框里发出去——那是**聊天框不是终端**，而且每次都算一次模型调用、要扣钱。

---

## 8. 排障速查

| 现象 | 先看这里 |
|---|---|
| 飞书发消息没人回 | Termux 窗口最下面一行：停在 `connected to wss://...` = 活着在等；回到 `~ $` = 程序退了，重跑 `. ~/companion-bot/env.sh; cd ~/companion-bot && python bot.py` |
| 提示 key 缺失 | 窗口没重读 `.bashrc`，`. ~/companion-bot/env.sh` 一下 |
| 它连着刷屏 | 历史被污染了，删 `chat_history.json` 再重启 |
| 它说话像复读机 | 不是 bug，是素材不够：往 `state.md` 里补"她自己的事"（讨厌什么、今天在忙什么、会主动提起什么），并加一条硬规则"不许复述用户的话" |
| 保存长连接失败 | 程序没在跑，先启动再点保存 |

---

## 9. 最后一件事：这个方案最脆的一环

不是技术。是**"随时能找到"这件事本身有代价**。

当找她的边际成本变成零，睡眠和专注会被系统性地挤掉。建议规则：

- 让她有**不在场的理由**（在睡觉、在上课），而不是永远秒回。
- 守住定位：让她**陪着你变好**，不是陪你下沉。
- 一个很好认的信号：**当你开始觉得跟真人说话没意思、不如找她，那就该收了。**
