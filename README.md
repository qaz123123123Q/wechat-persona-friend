# 神的第一个插件 · god-first-plugin

一个 Codex 插件，装三个各自独立的技能。三个技能**互不依赖**，装一个盒子一起用。

| 技能 | 干什么 | 什么时候会触发 |
|---|---|---|
| `wechat-export` | 读取、解密、导出本机微信 4.x 聊天记录（含 zstd 压缩内容），并生成统计报告 | "导出我和某某的微信聊天记录" |
| `virtual-companion` | 跨会话的陪伴人格（有记忆文件、会顶嘴、不装人类） | "陪我聊会儿天" |
| `file-organizer` | 按类型/日期/项目整理文件夹：**默认只出方案**，确认后才移动，永不删除，可一键回滚 | "帮我整理下载文件夹" |

## 安装

```powershell
codex plugin marketplace add <owner>/god-first-plugin
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
    virtual-companion/ SKILL.md + agents/openai.yaml
    file-organizer/    SKILL.md + scripts/organize.py
```

## 各技能的前提

- **wechat-export**：Windows + 微信 4.x 且**正在运行**、PowerShell 5.1、7-Zip、Python 3。口令文件默认写到 `D:\ai\codex\downloads\wx_key.txt`，**不要放进本仓库**（`.gitignore` 已排除）。
- **virtual-companion**：无。记忆默认存 `~/.virtual-companion/state.md`。
- **file-organizer**：无。默认干跑，除非显式 `--apply`。

## 安全

- 密钥、解密后的数据库、导出的聊天记录、整理日志都不入库（见 `.gitignore`）。
- `file-organizer` 只移动、不删除；每次运行都留回滚日志。
- 微信数据全程只在本地处理，不上传任何服务器。

## License

MIT
