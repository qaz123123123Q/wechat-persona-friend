<#
.SYNOPSIS
  提取微信 4.x（Windows）的 SQLCipher 口令。
  口令 = Weixin.dll 内嵌的 32 字节 XOR Weixin.exe 进程内存里的 32 字节。
  必须让微信保持运行（内存里才有那半段）。
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File find_key.ps1 `
    -DataRoot 'D:\xwechat_files\wxid_xxx_abcd\db_storage' `
    -OutFile  'D:\ai\codex\downloads\wx_key.txt'
#>
param(
  [Parameter(Mandatory = $true)][string]$DataRoot,     # ...\db_storage
  [string]$WeixinDll = '',                             # 留空自动找最新版
  [string]$VerifyDb  = '',                             # 留空用 message\message_0.db
  [string]$OutFile   = '',                             # 留空用 D:\ai\codex\downloads\wx_key.txt
  [switch]$Force                                       # 强制重新扫描（默认先复用已有口令）
)
$ErrorActionPreference = 'Stop'

$lib = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\WxKey.dll'
if (-not (Test-Path -LiteralPath $lib)) { throw "找不到 WxKey.dll：$lib" }
[void][Reflection.Assembly]::LoadFrom($lib)

if (-not (Test-Path -LiteralPath $DataRoot)) { throw "DataRoot 不存在：$DataRoot" }
if (-not $VerifyDb) { $VerifyDb = Join-Path $DataRoot 'message\message_0.db' }
if (-not (Test-Path -LiteralPath $VerifyDb)) { throw "验证用数据库不存在：$VerifyDb" }
if (-not $OutFile) {
  $OutFile = 'D:\ai\codex\downloads\wx_key.txt'
}
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null

# 0) 已有口令就先验证再复用：密钥没变就别再扫一遍（省约 1 分钟）
if (-not $Force -and (Test-Path -LiteralPath $OutFile)) {
  $line = Get-Content -LiteralPath $OutFile -Encoding UTF8 |
          Where-Object { $_ -match '^FINAL_KEY\s+[0-9a-fA-F]{64}' } | Select-Object -First 1
  if ($line) {
    $old = ([regex]::Match($line, '[0-9a-fA-F]{64}')).Value
    Write-Output '[0/3] 发现已有口令文件，先验证是否还有效'
    $chk = [WxKey]::VerifyKeys($VerifyDb, ('0' * 64), $old)
    if ($chk -match 'MATCH') {
      Write-Output '      仍然有效 → 复用，跳过扫描'
      Write-Output ''
      Write-Output '完成（复用）。'
      Write-Output ("  口令文件：" + $OutFile)
      Write-Output ("  FINAL_KEY " + $old)
      return
    }
    Write-Output '      已失效（可能换过账号或微信升级），改为重新扫描'
  }
}

if (-not $WeixinDll) {
  $base = 'C:\Program Files\Tencent\Weixin'
  $dirs = @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
            Sort-Object -Property Name -Descending)
  foreach ($d in $dirs) {
    $p = Join-Path $d.FullName 'Weixin.dll'
    if (Test-Path -LiteralPath $p) { $WeixinDll = $p; break }
  }
}
if (-not $WeixinDll) { throw '没找到 Weixin.dll，请用 -WeixinDll 手动指定' }

Write-Output "[1/3] 扫描 DLL 内嵌密钥：$WeixinDll"
$dllOut = [WxKey]::ScanDllForInternalKeys($WeixinDll)
$internals = @($dllOut -split "`n" | Where-Object { $_.StartsWith('KEY ') } |
               ForEach-Object { $_.Substring(4).Trim() })
Write-Output ("      内嵌候选：" + $internals.Count)
if ($internals.Count -eq 0) { throw 'DLL 里没扫到内嵌密钥，微信可能升级了，见 references\key-recovery.md' }

Write-Output '[2/3] 扫描 Weixin.exe 进程内存'
$proc = Get-Process Weixin -ErrorAction SilentlyContinue |
        Sort-Object WorkingSet64 -Descending | Select-Object -First 1
if (-not $proc) { throw '微信没在运行（找不到 Weixin 进程），请先登录微信' }
Write-Output ("      pid=" + $proc.Id)
$memOut = [WxKey]::ScanProcessMemory($proc.Id)
$cands = @($memOut -split "`n" | Where-Object { $_.StartsWith('CAND ') } |
           ForEach-Object { $_.Substring(5).Trim() })
Write-Output ("      内存候选：" + $cands.Count)
if ($cands.Count -eq 0) { throw '内存里没扫到候选，微信可能升级了，见 references\key-recovery.md' }

Write-Output '[3/3] 用数据库第 1 页 HMAC 验证'
$sw = [Diagnostics.Stopwatch]::StartNew()
$res = [WxKey]::VerifyKeys($VerifyDb, ($cands -join "`n"), ($internals -join "`n"))
$sw.Stop()
Write-Output ("      耗时 " + [math]::Round($sw.Elapsed.TotalSeconds, 1) + " 秒")

$final = ($res -split "`n" | Where-Object { $_ -like 'FINAL_KEY*' } | Select-Object -First 1)
if (-not $final) {
  Write-Output $res
  throw '验证失败：没有产出 FINAL_KEY'
}

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$content = @(
  "# 微信 4.x SQLCipher 口令（敏感：可解开全部历史数据）"
  "# 生成时间：$stamp"
  "# 数据目录：$DataRoot"
  $final
) -join "`r`n"
$content | Set-Content -LiteralPath $OutFile -Encoding UTF8

# 顺手确认每个库是不是同一个口令（salt 不同 → AES 密钥不同，但口令相同）
Write-Output ''
Write-Output '完成。'
Write-Output ("  口令文件：" + $OutFile)
Write-Output ("  " + $final)
Write-Output '  下一步：decrypt_db.ps1 或 bulk_decrypt.ps1 -KeyFile <上面这个文件>'
