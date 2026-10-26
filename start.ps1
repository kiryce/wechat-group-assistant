$ErrorActionPreference='Stop'
$projectDir=Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process -FilePath (Join-Path $projectDir '.venv/Scripts/pythonw.exe') -ArgumentList ('"'+(Join-Path $projectDir 'start_background_wechat.py')+'"') -WorkingDirectory $projectDir -WindowStyle Hidden
$pidFile=Join-Path $projectDir 'private/server.pid'
$alreadyRunning=$false
if (Test-Path -LiteralPath $pidFile) {
  $savedPid=[int](Get-Content -LiteralPath $pidFile)
  $processInfo=Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid"
  if ($processInfo -and $processInfo.CommandLine -like '*wechat-group-assistant*app.py*') { $alreadyRunning=$true }
}
if (!$alreadyRunning) {
  $server=Start-Process -FilePath (Join-Path $projectDir '.venv/Scripts/pythonw.exe') -ArgumentList ('"'+(Join-Path $projectDir 'app.py')+'"') -WorkingDirectory $projectDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $projectDir 'private/server.log') -RedirectStandardError (Join-Path $projectDir 'private/server-error.log')
  $server.Id | Set-Content -LiteralPath $pidFile
}
Start-Process 'http://127.0.0.1:8765'
