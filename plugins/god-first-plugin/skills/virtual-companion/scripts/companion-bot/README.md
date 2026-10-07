# companion-bot · 把人格挂到飞书上

把一个带「陪伴人格 + 记忆文件」的大模型包成飞书机器人，跑在旧安卓手机的 Termux 里。
这样电脑可以关，打开飞书就能跟她说话。

**完整的部署步骤（含四个真实踩过的坑）在**：

> `../../references/deploy-feishu.md`

## 这里有什么

| 文件 | 说明 |
|---|---|
| `bot.py` | 主程序：飞书长连接 → 拼人格和记忆 → 调模型 → 拆成短句发回去 |
| `install-termux.sh` | 手机上一条龙安装（已避开 `pkg update` 那个坑） |
| `config.example.json` | 配置模板，复制成 `config.json` 再填 |
| `requirements.txt` | 只有一个依赖：`lark-oapi` |
| `memory/state.example.md` | 记忆文件模板 |

程序每一轮都会把「现在几点 + 她此刻大概在干嘛」塞进系统提示（`bot.py` 里的
`time_block()` 和那两张作息表）。**没有这一段，模型会答"我不知道现在几点"**，
也编不出"她为什么没立刻回"。作息表是示例，按你的人设改。

## 最快的路径

```bash
# 手机上（Termux）
sh install-termux.sh          # 装依赖 + 铺记忆文件
# 然后编辑 config.json：app_id / app_secret / persona_name
python bot.py                 # 看到「正在建立长连接」再去飞书后台点保存
```

## 三条安全约定

1. **API key 走环境变量**（`env.sh` / `~/.bashrc`），不写进 `config.json`。
2. `config.json`、`env.sh`、`.env`、聊天记录**一律不进 git**（本目录 `.gitignore` 已排除）。
3. 聊天内容会经过飞书服务器和模型厂商服务器——知道这一点再决定聊什么。

## 人格从哪来

`state.md` 里写什么，她就记得什么。写空 = 一个没有记忆的普通聊天机器人。
想让她真的像某个人，先跑 `wechat-export` 导出聊天记录，再按 `virtual-companion` 的技能流程提炼成 `state.md`。
