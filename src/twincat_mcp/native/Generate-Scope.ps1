# Offline model creation and serialization only. Never start recording or connect ADS.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$errorWriter = [IO.StreamWriter]::new([Console]::OpenStandardError(), [Text.UTF8Encoding]::new($false))
$errorWriter.AutoFlush = $true
[Console]::SetError($errorWriter)
$temporaryPath = $null
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (@($request.signals).Count -lt 1 -or @($request.signals).Count -gt 128) { throw 'Use 1..128 signals.' }
    $apiPath = 'C:\TwinCAT\Functions\TE130x-Scope-View\Redist\TwinCAT.Measurement.Scope.API.dll'
    if (-not (Test-Path -LiteralPath $apiPath -PathType Leaf)) { throw 'Installed Beckhoff Scope API was not found.' }
    $assembly = [Reflection.Assembly]::LoadFrom($apiPath)
    $project = New-Object TwinCAT.Measurement.Scope.API.Model.ScopeProject
    $project.Name = [string]$request.name
    $project.MainServerExchange = '127.0.0.1.1.1'
    $charts = @{}
    $axes = @{}
    $groupRequests = @{}
    $sizes = @{ BIT=1; INT8=1; UINT8=1; INT16=2; UINT16=2; INT32=4; UINT32=4; INT64=8; UINT64=8; REAL32=4; REAL64=8 }
    foreach ($signal in $request.signals) {
        $chartKey = [string]$signal.chart
        if (-not $charts.ContainsKey($chartKey)) {
            $chart = New-Object TwinCAT.Measurement.Scope.API.Model.YTChart
            $chart.Name = $chartKey
            $project.AddMember($chart)
            $charts[$chartKey] = $chart
        }
        $axisKey = $chartKey + [char]0 + [string]$signal.axis
        if (-not $axes.ContainsKey($axisKey)) {
            $axis = New-Object TwinCAT.Measurement.Scope.API.Model.AxisGroup
            $axis.Name = [string]$signal.axis
            $charts[$chartKey].AddMember($axis)
            $axes[$axisKey] = $axis
            $groupRequests[$axisKey] = @{ chart=$chartKey; axis=[string]$signal.axis }
        }
        $channel = New-Object TwinCAT.Measurement.Scope.API.Model.Channel
        $axes[$axisKey].AddMember($channel)
        $acquisition = New-Object TwinCAT.Measurement.Scope.API.Model.AdsAcquisition
        $acquisition.SymbolName = [string]$signal.symbol
        $acquisition.SymbolBased = $true
        $acquisition.AmsNetIdExchange = [string]$signal.ams_net_id
        $acquisition.TargetPort = [int]$signal.ads_port
        $acquisition.UseLocalServer = $true
        $acquisition.UseTaskSampleTime = $true
        $kind = [string]$signal.scope_type
        if (-not $sizes.ContainsKey($kind)) { throw 'Unsupported Scope primitive type.' }
        $acquisition.DataType = [Enum]::Parse([TwinCAT.Scope2.Communications.Scope2DataType], $kind)
        $acquisition.VariableSize = $sizes[$kind]
        $acquisition.IndexOffset = 0
        $acquisition.FileHandle = 0
        $acquisition.ServerHandle = 0
        $project.AddMember($acquisition)
        $interpreter = New-Object TwinCAT.Measurement.Scope.API.Model.AcquisitionInterpreter($acquisition)
        $channel.AddMember($interpreter)
    }
    $xmlText = $project.SaveToString()
    # Reload only our own freshly generated XML, never user-provided XML/assembly paths.
    $temporaryPath = [IO.Path]::Combine([IO.Path]::GetTempPath(), ('twincat-mcp-' + [guid]::NewGuid().ToString('N') + '.tcscopex'))
    $stream = [IO.File]::Open($temporaryPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($xmlText)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
    $loaded = [TwinCAT.Measurement.Scope.API.Model.ScopeProject]::LoadScopeProject($temporaryPath)
    if ($loaded.Name -ne $project.Name) { throw 'Scope model reload did not preserve the project name.' }
    $queue = New-Object 'System.Collections.Generic.Queue[object]'
    $queue.Enqueue($loaded)
    $acquisitions = @()
    $bindings = @()
    while ($queue.Count -gt 0) {
        $member = $queue.Dequeue()
        if ($member -is [TwinCAT.Measurement.Scope.API.Model.AdsAcquisition]) {
            $acquisitions += $member.Guid.ToString()
        }
        if ($member -is [TwinCAT.Measurement.Scope.API.Model.AcquisitionInterpreter]) {
            if ($null -eq $member.Acquisition) { throw 'Reloaded channel has no bound acquisition.' }
            $bindings += $member.Acquisition.Guid.ToString()
        }
        if ($null -ne $member.PSObject.Properties['SubMember']) {
            foreach ($child in $member.SubMember) { $queue.Enqueue($child) }
        }
    }
    if ($acquisitions.Count -ne @($request.signals).Count -or $bindings.Count -ne $acquisitions.Count) { throw 'Scope reload changed acquisition/channel counts.' }
    if (@(Compare-Object ($acquisitions | Sort-Object) ($bindings | Sort-Object)).Count -ne 0) { throw 'Scope reload binding mismatch.' }
    $groupMap = @($axes.Keys | Sort-Object | ForEach-Object {
        $source = $groupRequests[$_]
        @{ requestedChart=$source.chart; requestedAxis=$source.axis; chart=$charts[$source.chart].Name; axis=$axes[$_].Name }
    })
    $result = @{ xml=$xmlText; groups=$groupMap; nativeReloadVerified=$true; apiVersion=$assembly.GetName().Version.ToString();
                 acquisitions=$acquisitions.Count; channelBindings=$bindings.Count; onlineAvailability='not_verified' }
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 6 -Compress))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $temporaryPath -and [IO.File]::Exists($temporaryPath)) { [IO.File]::Delete($temporaryPath) }
}
