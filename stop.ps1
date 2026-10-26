$projectDir=Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile=Join-Path $projectDir 'private/server.pid'
if (Test-Path -LiteralPath $pidFile) {
  $savedPid=[int](Get-Content -LiteralPath $pidFile)
  $processInfo=Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid"
  if ($processInfo -and $processInfo.CommandLine -like '*wechat-group-assistant*app.py*') { Stop-Process -Id $savedPid }
}
