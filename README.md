# 神的第一个插件 · god-first-plugin

一个 Codex 插件，装三个各自独立的技能。三个技能**互不依赖**，装一个盒子一起用。

它们还能串成一条链：**读微信 → 提炼人格 → 挂到手机上常驻**（见下面的"完整链路"）。

| 技能 | 干什么 | 什么时候会触发 |
|---|---|---|
| `wechat-export` | 读取、解密、导出本机微信 4.x 聊天记录（含 zstd 压缩内容），并生成统计报告 | "导出我和某某的微信聊天记录" |
| `virtual-companion` | 跨会话的陪伴人格（有记忆文件、会顶嘴、不装人类）；**能挂到手机上常驻** | "陪我聊会儿天" |
| `file-organizer` | 按类型/日期/项目整理文件夹：**默认只出方案**，确认后才移动，永不删除，可一键回滚 | "帮我整理下载文件夹" |

## 安装

```powershell
codex plugin marketplace add <owner>/wechat-reader
codex plugin add god-first-plugin@god-first-plugin
```

装完**新开一个对话**再说需求（Codex 只在新线程加载新技能）。

## 目录

```
.agents/plugins/marketplace.json          市场清单
plugins/god-first-plugin/
  .codex-plugin/plugin.json               插件身份证
  skills/
    wechat-export/     SKILL.md + references + scripts + lib(WxKey.dll)
    virtual-companion/ SKILL.md + agents/ + references/deploy-feishu.md
                       scripts/companion-bot/   (飞书长连接机器人，跑在 Termux 里)
    file-organizer/    SKILL.md + scripts/organize.py
```

## 完整链路：从微信记录到手机上的她

这三件事本来是分开的技能，但真实用起来是一条链，**后一步依赖前一步的产物**：

```text
① wechat-export        导出本机微信聊天记录（含 zstd 解码 + 统计）
        |
        v
② virtual-companion    从记录里提炼"她怎么说话"，沉淀成 ~/.virtual-companion/state.md
        |
        v
③ companion-bot        把 state.md + 一个大模型，挂到飞书长连接上
                        -> 跑在旧安卓手机的 Termux 里，电脑可以关
```

**第 ③ 步解决了什么**：飞书开放平台支持"使用长连接接收回调"——程序主动往外连一条
WebSocket。所以**不需要公网 IP、不需要内网穿透、不需要备案、不用买服务器**，
而且不像微信个人号机器人那样违反服务协议。

部署步骤（含四个真实踩过的坑）见
`plugins/god-first-plugin/skills/virtual-companion/references/deploy-feishu.md`。

## 各技能的前提

- **wechat-export**：Windows + 微信 4.x 且**正在运行**、PowerShell 5.1、7-Zip、Python 3。口令文件默认写到 `D:\ai\codex\downloads\wx_key.txt`，**不要放进本仓库**（`.gitignore` 已排除）。
- **virtual-companion**：本体无前提，记忆默认存 `~/.virtual-companion/state.md`。
  要挂到手机上另需：一台常开的安卓设备（装 Termux）+ 一个飞书自建应用 + 一个模型 API key。
- **file-organizer**：无。默认干跑，除非显式 `--apply`。

## 安全

- 密钥、解密后的数据库、导出的聊天记录、整理日志都不入库（见 `.gitignore`）。
- `file-organizer` 只移动、不删除；每次运行都留回滚日志。
- 微信数据全程只在本地处理，不上传任何服务器。
- 飞书机器人的 `config.json`（App Secret）与 `env.sh`（API key）**一律不入库**。
  另外要知道：挂到手机上之后，聊天内容会经过**飞书服务器**和**模型厂商服务器**，
  这两段躲不掉。

## License

MIT
