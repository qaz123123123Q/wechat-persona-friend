<#
.SYNOPSIS
  用口令解密微信 4.x 的数据库（可指定只解某几个库）。
.EXAMPLE
  ... decrypt_db.ps1 -DataRoot 'D:\xwechat_files\wxid_xxx_abcd\db_storage' `
    -OutRoot 'D:\ai\codex\downloads\wx_decrypted' -KeyFile 'D:\ai\codex\downloads\wx_key.txt' `
    -Include 'contact\contact.db','session\session.db','message\message_0.db','message\message_1.db'
#>
param(
  [Parameter(Mandatory = $true)][string]$DataRoot,
  [Parameter(Mandatory = $true)][string]$OutRoot,
  [string]$KeyFile = '',
  [string]$Pass = '',
  [string[]]$Include = @()
)
$ErrorActionPreference = 'Stop'

$lib = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\WxKey.dll'
if (-not (Test-Path -LiteralPath $lib)) { throw "找不到 WxKey.dll：$lib" }
[void][Reflection.Assembly]::LoadFrom($lib)

if (-not $Pass) {
  if (-not $KeyFile) { throw '必须给 -KeyFile 或 -Pass' }
  $line = Get-Content -LiteralPath $KeyFile -Encoding UTF8 |
          Where-Object { $_ -match '^FINAL_KEY\s+([0-9a-fA-F]{64})' } | Select-Object -First 1
  if (-not $line) { throw "口令文件里没有 FINAL_KEY 行：$KeyFile" }
  $Pass = ([regex]::Match($line, '([0-9a-fA-F]{64})')).Groups[1].Value
}

if ($Include.Count -gt 0) {
  $files = @($Include | ForEach-Object { Get-Item -LiteralPath (Join-Path $DataRoot $_) -ErrorAction SilentlyContinue } |
             Where-Object { $_ })
} else {
  $files = @(Get-ChildItem -LiteralPath $DataRoot -Recurse -Filter '*.db' -File |
             Where-Object { $_.Name -notlike '*-wal' -and $_.Name -notlike '*-shm' })
}
Write-Output ("待解密：" + $files.Count + " 个库")

$ok = 0; $bad = 0
foreach ($f in $files) {
  $rel = $f.FullName.Substring($DataRoot.Length).TrimStart('\')
  $dst = Join-Path $OutRoot $rel
  New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
  try {
    $log = [WxKey]::DecryptDb($f.FullName, $dst, $Pass)
    $okLine = @($log -split "`r?`n" | Where-Object { $_.Trim() -like 'ok=*' } | Select-Object -First 1)
    if ($okLine.Count -gt 0 -and $okLine[0].Trim() -eq 'ok=True') {
      $ok++; Write-Output ("  OK   " + $rel)
    } else {
      $bad++; Write-Output ("  FAIL " + $rel + "  " + ($log -replace "`n", ' | '))
    }
  } catch {
    $bad++; Write-Output ("  ERR  " + $rel + "  " + $_.Exception.Message)
  }
}
Write-Output ("完成：ok=" + $ok + " 失败=" + $bad)
if ($bad -gt 0) { Write-Output '提示：口令错、或该库属于另一个账号。逐个库的口令相同、AES 密钥不同，不需要手工干预。' }
