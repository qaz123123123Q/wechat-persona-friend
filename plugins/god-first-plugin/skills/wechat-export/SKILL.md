---
name: wechat-export
description: 读取、解密、导出并分析本机微信 4.x（Windows）的聊天记录。当用户要求"导出某个人的微信聊天记录""分析某个微信会话""读取本机微信数据""微信数据库解密""微信群聊记录"时使用。覆盖密钥提取（DLL 内嵌密钥 XOR 进程内存密钥）、逐库 SQLCipher 解密、任意会话导出（含 zstd 压缩内容解码）、以及统计分析。
---

# 微信聊天记录读取 / 导出 / 分析

## 一句话原理

微信 4.x（Windows）把聊天记录存在本机 SQLCipher 加密库里。口令 = **`Weixin.dll` 内嵌的 32 字节** XOR **`Weixin.exe` 进程内存里的 32 字节**。两段都取到后，口令可以解开该账号下的**全部** `.db`（每个库的 AES 密钥由口令 + 该库自己的 16 字节 salt 派生，所以必须逐库解）。

## 前置条件

1. Windows + 微信 4.x 桌面版，**微信必须正在运行**（内存里才有那半段密钥）。
2. 数据目录一般在 `D:\xwechat_files\<wxid>_<后缀>\db_storage`（也常见于 `%USERPROFILE%\Documents\xwechat_files\...`）。目录名里的 `wxid_...` 就是机主自己的 wxid。
3. Python（本机可用 `C:\Users\z1513\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`）、PowerShell 5.1（不要用 pwsh 7 做 COM/WinRT 相关的事）、7-Zip（解 zstd 用）。

## 标准流程

### 步骤 1 找密钥

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <plugin>\skills\wechat-export\scripts\find_key.ps1 `
  -DataRoot 'D:\xwechat_files\<wxid>_<后缀>\db_storage' `
  -OutFile 'D:\ai\codex\downloads\wx_key.txt'
```

- 脚本会：① 扫 `Weixin.dll` 拿内嵌密钥候选；② 扫 `Weixin.exe` 内存拿候选；③ 用真实数据库第 1 页的 HMAC 验证，输出 `FINAL_KEY`。
- `-OutFile` 留空时默认写 `D:\ai\codex\downloads\wx_key.txt`。
- **会先复用**：如果口令文件已存在且仍然有效，脚本 1~2 秒就返回，跳过约 50 秒的扫描；失效才重扫。要强制重扫加 `-Force`。
- **密钥文件放到插件目录之外**（能解开全部历史数据，且改密码也换不掉旧数据）。插件里绝不放密钥。

### 步骤 2 解密数据库

全部解密（约 180 MB，含所有会话/联系人/朋友圈/收藏）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <plugin>\skills\wechat-export\scripts\bulk_decrypt.ps1 `
  -DataRoot 'D:\xwechat_files\<wxid>_<后缀>\db_storage' `
  -OutRoot  'D:\ai\codex\downloads\wx_decrypted' `
  -KeyFile  'D:\ai\codex\downloads\wx_key.txt'
```

只要几个库（省时间、少碰隐私数据）——`decrypt_db.ps1` 的 `-Include` 收相对路径：

```powershell
... \scripts\decrypt_db.ps1 -DataRoot ... -OutRoot ... -KeyFile ... `
  -Include 'contact\contact.db','session\session.db','message\message_0.db','message\message_1.db'
```

### 步骤 3 导出会话

```powershell
python <plugin>\skills\wechat-export\scripts\export_chat.py `
  --db-root  'D:\ai\codex\downloads\wx_decrypted' `
  --identifier 'wxid_xxxxxxxxxxxx'      # 或微信号 / 昵称 / 备注的任意片段
  --out-dir  'D:\ai\codex\downloads\wx_export'
```

- 模糊匹配 contact 表；命中多个会列出候选让你换更精确的词。
- **会调用 7-Zip 解开 zstd 压缩的消息**（引用原文、拍一拍、通话时长、文件、位置、红包、撤回、语音秒数都在里面）。没有这步会丢掉大量内容。
- 产出 `<out-dir>\微信聊天记录_<备注>_<起>至<止>.txt` 和同名 `.json`。

### 步骤 4 分析

```powershell
python <plugin>\skills\wechat-export\scripts\analyze_chat.py --json '<out-dir>\messages_<标签>.json'
```

输出：双方条数/字数、内容类型分布、语音总时长、通话记录与时长、拍一拍次数、逐日表（条数/首条发起者/首末时间/跨度）、接话间隔中位数、凌晨占比、最长沉默段、金钱/见面/感情等敏感关键词命中。

## 数据结构（写脚本时要用的）

- **联系人**：`contact\contact.db` → 表 `contact`，字段 `username`(wxid)、`alias`(微信号)、`nick_name`、`remark`、`delete_flag`。群在 `chat_room` + `chatroom_member`(room_id/member_id 指 `name2id` 的 rowid)。
- **消息**：`message\message_0.db`、`message_1.db`（**分片，两个都要查**）。每个分片**各有自己的 `Name2Id` 表**（rowid→username），必须按分片分别映射。
- 每个会话一张表：`Msg_<md5(wxid)>`。
- 字段：`local_id`、`local_type`、`real_sender_id`、`create_time`(Unix 秒)、`message_content`、`WCDB_CT_message_content`。
- **local_type 解码**：低 32 位主类型 / 高位子类型。
  - 主类型：1 文本｜3 图片｜34 语音｜43 视频｜47 表情贴纸｜48 位置｜49 卡片类｜50 通话｜10000 系统
  - 49 的子类型：5 链接｜6 文件｜17 位置共享｜19 合并转发｜33 小程序｜57 引用｜**62 拍一拍**｜2001 红包
- `WCDB_CT_message_content != 0` 表示内容被 **zstd** 压缩（本机没有 zstandard 库，但 `7z x file.zst` 能解）。

## 坑（都真实踩过）

1. **文件被占用**：微信在跑，库文件被独占。必须用 `FileAccess.Read + FileShare.ReadWrite | Delete` 打开。
2. **WAL 未合并**：`*.db-wal` 里可能压着最新数据。完全退出微信让它 checkpoint，再解密，能拿到最新几条。
3. **一个人可能有多个微信号**：用 `contact` + `session\session.db` 的 `SessionTable` 交叉比对（alias 里可能夹生日本身数字，容易误判同一个人）。
4. **分片**：同一会话可能同时存在于 `message_0` 和 `message_1`，必须合并去重后按时间排序。
5. **每个库的 AES 密钥不同**：口令相同，但 AES 密钥 = `PBKDF2(口令, 该库前 16 字节 salt, 256000, SHA512)`，不能复用别的库的密钥。
6. **页布局**：页大小 4096、预留 80 字节（16 IV + 64 HMAC）；第 1 页开头 16 字节是 salt，解密后要把 `SQLite format 3\0` 补回去才是合法 SQLite。
7. **C# 编译**：用 `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`，`/unsafe /target:library /reference:System.Drawing.dll`；PowerShell 5.1 的 `Add-Type` 不支持 `-CompilerOptions`。
8. **不要相信"卡片""空"**：没解 zstd 时，图片/表情/引用/拍一拍会显示成 `[卡片]`，长消息会显示成 `[空]`。看到这些先怀疑解码没做完。

## 安全与清理

- 密钥文件、`decrypted\`（全量数据）、导出的 json/txt 都属于敏感数据；分析完把 `decrypted\`、密钥文件、导出的中间 json 删掉或移到自己控制的位置。
- 只在用户本人的机器上、对用户自己的账号使用；不要把这些数据上传到任何地方。
