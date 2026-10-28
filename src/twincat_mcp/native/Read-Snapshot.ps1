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
    $action = if ($null -ne $request.PSObject.Properties['action']) { [string]$request.action } else { 'snapshot' }
    if ($action -notin @('snapshot','describe','browse','state','stream')) { throw 'Unsupported read-only action.' }
    if ($names.Count -gt 128 -or ($names.Count -lt 1 -and $action -notin @('state','browse'))) { throw 'Use 1..128 symbols.' }
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
    if ($action -eq 'stream') {
        $duration = [double]$request.durationSeconds
        $interval = [int]$request.intervalMs
        $maximum = [int]$request.maxSamples
        if ($duration -lt 0.1 -or $duration -gt 3600 -or $interval -lt 20 -or $interval -gt 60000 -or $maximum -lt 1 -or $maximum -gt 100000) { throw 'Invalid stream limits.' }
        $watch = [Diagnostics.Stopwatch]::StartNew()
        for ($sequence=0; $sequence -lt $maximum -and $watch.Elapsed.TotalSeconds -lt $duration; $sequence++) {
            $snapshot = $reader.Capture($names)
            $snapshot | Add-Member -NotePropertyName Sequence -NotePropertyValue $sequence
            $snapshot | Add-Member -NotePropertyName ElapsedSeconds -NotePropertyValue $watch.Elapsed.TotalSeconds
            [Console]::Out.WriteLine(($snapshot | ConvertTo-Json -Depth 8 -Compress))
            [Console]::Out.Flush()
            $wait = $interval - [int]($watch.Elapsed.TotalMilliseconds % $interval)
            if ($watch.Elapsed.TotalSeconds -lt $duration) { Start-Sleep -Milliseconds $wait }
        }
    } elseif ($action -in @('describe','browse','state')) {
        $snapshot = $reader.Describe($names, ($action -eq 'browse'))
        if ($action -eq 'browse') {
            $matches = @($snapshot.Symbols | Where-Object { $_.Name -like [string]$request.nameFilter })
            $total = $matches.Count
            $snapshot.Symbols = [TwinCatReadOnly.Symbol[]]@($matches | Select-Object -Skip ([int]$request.offset) -First ([int]$request.limit))
            $snapshot | Add-Member -NotePropertyName Total -NotePropertyValue $total
        }
        [Console]::Out.WriteLine(($snapshot | ConvertTo-Json -Depth 8 -Compress))
    } else {
        $snapshot = $reader.Capture($names)
        [Console]::Out.WriteLine(($snapshot | ConvertTo-Json -Depth 8 -Compress))
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $transport) { $transport.Dispose() }
}
