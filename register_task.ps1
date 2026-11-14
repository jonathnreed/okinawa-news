# 毎日自動でお知らせを取得するよう、Windows タスクスケジューラに登録します。
# 使い方: このフォルダで  powershell -ExecutionPolicy Bypass -File register_task.ps1
#   時刻を変える場合:     ... -File register_task.ps1 -Time 06:30
#   登録を解除する場合:   Unregister-ScheduledTask -TaskName OkinawaGovNews -Confirm:$false
param([string]$Time = "07:00")

$dir = $PSScriptRoot
$py = (Get-Command python -ErrorAction Stop).Source
$pyw = Join-Path (Split-Path $py) "pythonw.exe"   # 黒い画面を出さずに実行
if (-not (Test-Path $pyw)) { $pyw = $py }

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\fetch_news.py`"" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
# PCが指定時刻にスリープ/電源オフだった場合は、次に起動したときに実行
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "OkinawaGovNews" -Action $action -Trigger $trigger -Settings $settings `
  -Description "沖縄県・市町村のお知らせを取得して index.html を更新" -Force | Out-Null
Write-Host "登録しました: 毎日 $Time に $dir\fetch_news.py を実行します。"
