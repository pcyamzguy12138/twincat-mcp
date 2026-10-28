$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$errorWriter = [IO.StreamWriter]::new([Console]::OpenStandardError(), [Text.UTF8Encoding]::new($false))
$errorWriter.AutoFlush = $true
[Console]::SetError($errorWriter)
$transport = $null
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $names = [string[]]@($request.symbols)
    if ($names.Count -lt 1 -or $names.Count -gt 128) { throw 'Use 1..128 symbols.' }
    $dllCandidates = if ([Environment]::Is64BitProcess) {
        @('C:\TwinCAT\Common64\TcAdsDll.dll', 'C:\TwinCAT\AdsApi\TcAdsDll\x64\TcAdsDll.dll')
    } else {
        @('C:\TwinCAT\Common32\TcAdsDll.dll', 'C:\TwinCAT\AdsApi\TcAdsDll\TcAdsDll.dll')
    }
    $dll = $dllCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dll) { throw 'Installed Beckhoff TcAdsDll.dll not found.' }
    Add-Type -Path (Join-Path $PSScriptRoot 'AdsReadOnly.cs')
    $transport = New-Object TwinCatReadOnly.NativeTransport($dll, [string]$request.amsNetId, [int]$request.adsPort, [int]$request.timeoutMs)
    $reader = New-Object TwinCatReadOnly.Reader($transport)
    $snapshot = $reader.Capture($names)
    [Console]::Out.WriteLine(($snapshot | ConvertTo-Json -Depth 8 -Compress))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $transport) { $transport.Dispose() }
}
