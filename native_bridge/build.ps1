param([string]$Compiler="clang++.exe")
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  & $Compiler -std=c++17 -O2 -shared -static -Wno-deprecated-literal-operator -Wno-tautological-constant-out-of-range-compare native_bridge/bridge.cpp native_bridge/proxy.cpp native_bridge/send.cpp native_bridge/version.def -Ivendor/WeChat-Hook/include -lws2_32 -lcrypt32 -o native_bridge/version.dll
  if ($LASTEXITCODE -ne 0) { throw 'Bridge compilation failed' }
} finally { Pop-Location }
