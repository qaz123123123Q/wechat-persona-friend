# 微信 4.x 本地数据结构速查

## 目录

```
<数据根>\db_storage\
  contact\contact.db          联系人（含群）
  session\session.db          会话表（last_timestamp 判断最近聊天）
  message\message_0.db        消息分片 0
  message\message_1.db        消息分片 1
  message\message_fts.db      全文索引
  message\media_0.db          媒体元数据
  message\biz_message_*.db    公众号消息
  sns\sns.db                  朋友圈
  favorite\favorite.db        收藏
  head_image\head_image.db    头像
```

## 关键表

| 表 | 说明 |
|---|---|
| `contact` | `username`(wxid) / `alias`(微信号) / `nick_name` / `remark` / `delete_flag` |
| `chat_room` | 群的 `username` + `owner` + `ext_buffer` |
| `chatroom_member` | `room_id` / `member_id`（都指向 `name2id` 的 rowid） |
| `chat_room_info_detail` | 群公告等 |
| `SessionTable` | `username` / `last_timestamp` / `summary` / `unread_count` |
| `Msg_<md5(wxid)>` | 每个会话一张表，字段见下 |
| `Name2Id`（每个 message 分片各一份） | `rowid` → `user_name` |

## Msg_ 表字段

| 字段 | 含义 |
|---|---|
| `local_id` | 会话内自增序号 |
| `local_type` | 低 32 位主类型，高位子类型 |
| `real_sender_id` | 发送者，指向本分片的 `Name2Id` |
| `create_time` | Unix 秒 |
| `message_content` | 文本 / XML / 二进制（可能是 zstd 压缩） |
| `WCDB_CT_message_content` | != 0 表示 zstd 压缩 |

## local_type 对照

| 主类型 | 含义 |
|---|---|
| 1 | 文本 |
| 3 | 图片 |
| 34 | 语音（XML 里有 `voicelength`，毫秒） |
| 43 | 视频（XML 里有 `playlength`，秒） |
| 47 | 表情贴纸 |
| 48 | 位置 |
| 49 | 卡片类（看子类型） |
| 50 | 通话（XML 里 `通话时长 mm:ss` / `已取消` / `对方已拒绝`） |
| 10000 | 系统（添加好友、撤回、位置共享结束） |

| 49 的子类型 | 含义 |
|---|---|
| 5 | 链接 |
| 6 | 文件 |
| 17 | 位置共享 |
| 19 | 合并转发 |
| 33 | 小程序 |
| 57 | 引用回复（`<refermsg><content>` 是被引用的原文） |
| 62 | **拍一拍**（`<title>` + `<pattedusername>`） |
| 2001 | 红包 |

## zstd 解码

`WCDB_CT_message_content != 0` 的 `message_content` 是标准 zstd 帧（头 4 字节 `28 b5 2f fd`）。本机 Python 没有 zstandard 库，用 7-Zip：

```powershell
7z x -y -o<out> <dir>\*.zst        # 支持通配符批量解，一次调用能解几百个
```

解出来是 UTF-8 的 XML/文本；图片、语音等仍只有元数据，没有实际内容。
