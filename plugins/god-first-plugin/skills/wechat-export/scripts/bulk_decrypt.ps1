<#
.SYNOPSIS
  批量解密微信 4.x 的全部数据库（约 180 MB，含所有会话/联系人/朋友圈/收藏）。
.NOTES
  只解开 *.db，不含 -wal/-shm。最新几条消息可能在 WAL 里：完全退出微信让它 checkpoint 后再跑，最稳。
#>
param(
  [Parameter(Mandatory = $true)][string]$DataRoot,   # ...\db_storage
  [Parameter(Mandatory = $true)][string]$OutRoot,
  [string]$KeyFile = '',
  [string]$Pass = ''
)
$ErrorActionPreference = 'Stop'
$params = @{ DataRoot = $DataRoot; OutRoot = $OutRoot }
if ($KeyFile) { $params.KeyFile = $KeyFile }
if ($Pass) { $params.Pass = $Pass }
& (Join-Path $PSScriptRoot 'decrypt_db.ps1') @params
