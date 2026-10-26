$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE) { throw 'Python 3.12 is required' }
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
New-Item -ItemType Directory -Force vendor,private | Out-Null
function Get-Dependency($name,$url,$commit) {
  $path=Join-Path 'vendor' $name
  if (!(Test-Path $path)) { git clone $url $path; if ($LASTEXITCODE) { throw 'Clone failed' } }
  git -C $path checkout --detach $commit
  if ($LASTEXITCODE) { throw 'Pinned dependency checkout failed' }
}
Get-Dependency 'WeChatDataAnalysis' 'https://github.com/LifeArchiveProject/WeChatDataAnalysis.git' 'aa10c0940813e3dd41aed98e3b27f4bd8999ac28'
Get-Dependency 'WeChat-Hook' 'https://github.com/aixed/WeChat-Hook.git' 'a9d3f27e4afbe4af85ba72a0a9d93ef8f16e594f'
& ./.venv/Scripts/python.exe -c "import core; core.init_db()"
Write-Host 'Ready. See index.html for setup and usage.'
