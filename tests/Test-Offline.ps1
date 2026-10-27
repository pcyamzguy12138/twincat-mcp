$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$native = Join-Path $PSScriptRoot '../src/twincat_mcp/native'
$source = Get-Content -LiteralPath (Join-Path $native 'AdsReadOnly.cs') -Raw
$tests = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Test-Reader.cs') -Raw
Add-Type -TypeDefinition ($source + [Environment]::NewLine + $tests)
[TwinCatReadOnly.Tests.Checks]::Run() | ForEach-Object { Write-Host "PASS $_" }
$forbidden = 'AdsSyncWriteReq|AdsSyncWriteControl|AdsSyncAddDeviceNotification|AdsAddRoute|CreateVariableHandle'
if ($source -match $forbidden) { throw 'Forbidden ADS operation found.' }
if ($source -notmatch 'query\(port,ref target,0xF009,0,' -or $source -notmatch 'query\(port,ref target,0xF080,') {
    throw 'Expected read-only query whitelist missing.'
}
$tokens = $null; $errors = $null
[Management.Automation.Language.Parser]::ParseFile((Join-Path $native 'Read-Snapshot.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
Write-Host 'PASS bridge parser and read-only API checks. No ADS DLL loaded; no PLC accessed.'
