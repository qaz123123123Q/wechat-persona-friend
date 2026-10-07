# 密钥恢复原理与验证

## 两半密钥

- **DLL 侧**：`Weixin.dll` 代码段里有 4 组 `48 BA <8字节>` 连续出现（组间隔 3–8 字节），后面跟 `48 85 C0`；四段拼起来就是 32 字节内嵌密钥。
- **内存侧**：在 `MEM_COMMIT + MEM_PRIVATE` 区域找特征 `??×6` + `00×10 20 00×7 2F 00×7`，取匹配处前 8 字节当指针，读该地址 32 字节。
- **口令** = 内存候选 XOR DLL 内嵌候选。

`Weixin.dll` 路径随版本变化，例如 `C:\Program Files\Tencent\Weixin\4.1.15.13\Weixin.dll`（用 `find_key.ps1` 自动挑最新版本目录）。

## 验证（不用真正解密就能确认口令对不对）

```
AES_KEY  = PBKDF2(口令, salt, 256000, SHA512)
HMAC_KEY = PBKDF2(AES_KEY, salt XOR 0x3a, 2)
salt     = 数据库文件前 16 字节
校验      = HMAC-SHA512(HMAC_KEY, 第1页 [4032:4096]) == 文件里的 [4032:4096] 尾 64 字节
```

匹配即口令正确。`WxKey.dll` 的 `VerifyKeys(dbPath, candidates, internals)` 就是干这个的，输出里带 `FINAL_KEY`。

## 逐库解密

```
salt     = 该库前 16 字节
AES_KEY  = PBKDF2(口令, salt, 256000, SHA512)
页大小    = 4096，每页尾 80 字节 = 16 字节 IV + 64 字节 HMAC
第 1 页   解密后要把 "SQLite format 3\0" 补回开头
```

**每个库的 salt 不同 → AES 密钥不同**，所以必须逐库派生，不能把 A 库的密钥用到 B 库。

## 工具

- `lib\WxKey.cs` / `lib\WxKey.dll`：扫描 DLL 内嵌密钥、扫描进程内存、验证口令、解密单个库。
- 重新编译：`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /unsafe /target:library /out:WxKey.dll WxKey.cs`

## 失效场景

微信大版本升级后，DLL 特征可能变化，`find_key.ps1` 会报"没找到候选"。这时：

1. 先用 `[WxKey]::ProbePage1` / `BruteLayout` 确认页布局是否还是 4096+80。
2. 再按新版 `Weixin.dll` 重新定位内嵌密钥的指令特征。
3. 内存侧特征（`00×10 20 00×7 2F 00×7`）也可能变。
