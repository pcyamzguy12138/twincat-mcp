$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$errorWriter = [IO.StreamWriter]::new([Console]::OpenStandardError(), [Text.UTF8Encoding]::new($false))
$errorWriter.AutoFlush = $true
[Console]::SetError($errorWriter)
$dte = $null
$owned = $false
$lease = $null
$filter = $null
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    # A kernel job contains only the newly created automation instance. Closing
    # this bridge (including timeout termination) closes the job and that instance.
    $dteAssembly = 'C:\Program Files (x86)\Beckhoff\TcXaeShell\Common7\IDE\PublicAssemblies\envdte.dll'
    $dte80Assembly = 'C:\Program Files (x86)\Beckhoff\TcXaeShell\Common7\IDE\PublicAssemblies\envdte80.dll'
    Add-Type -Path $dteAssembly
    Add-Type -Path $dte80Assembly
    Add-Type -ReferencedAssemblies @($dteAssembly, $dte80Assembly) -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
[ComImport, Guid("00000016-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IBuildMessageFilter {
    [PreserveSig] int HandleInComingCall(int type, IntPtr caller, int ticks, IntPtr info);
    [PreserveSig] int RetryRejectedCall(IntPtr callee, int ticks, int rejection);
    [PreserveSig] int MessagePending(IntPtr callee, int ticks, int pending);
}
public sealed class BuildMessageFilter : IBuildMessageFilter, IDisposable {
    [DllImport("ole32.dll")] static extern int CoRegisterMessageFilter(IBuildMessageFilter value, out IBuildMessageFilter previous);
    IBuildMessageFilter old;
    public BuildMessageFilter() { Marshal.ThrowExceptionForHR(CoRegisterMessageFilter(this,out old)); }
    public int HandleInComingCall(int type,IntPtr caller,int ticks,IntPtr info) { return 0; }
    public int RetryRejectedCall(IntPtr callee,int ticks,int rejection) { return ticks<30000 && (rejection==1 || rejection==2) ? 200 : -1; }
    public int MessagePending(IntPtr callee,int ticks,int pending) { return 2; }
    public void Dispose() { IBuildMessageFilter ignored; CoRegisterMessageFilter(old,out ignored); }
}
public sealed class OfflineDiagnostic {
    public string description, file;
    public int line, column, severity;
}
public sealed class OfflineDiagnostics {
    public int Count;
    public OfflineDiagnostic[] Items;
}
public sealed class OfflineBuildLease : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct Io {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic BasicLimitInformation; public Io IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Extended info, uint size);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint process);
    IntPtr job;
    public static int WindowProcess(int hwnd) { uint id; GetWindowThreadProcessId(new IntPtr(hwnd),out id); return (int)id; }
    public static int DteProcess(object dte) { return WindowProcess(((EnvDTE._DTE)dte).MainWindow.HWnd); }
    public static OfflineDiagnostics Errors(object dte) {
        var errors=((EnvDTE80.DTE2)dte).ToolWindows.ErrorList.ErrorItems;
        var result=new OfflineDiagnostics {Count=errors.Count,Items=new OfflineDiagnostic[Math.Min(500,errors.Count)]};
        for(int i=0;i<result.Items.Length;i++) {
            var item=errors.Item(i+1);
            result.Items[i]=new OfflineDiagnostic {description=item.Description,file=item.FileName,line=item.Line,column=item.Column,severity=(int)item.ErrorLevel};
        }
        return result;
    }
    public static void Prepare(object dte) { var app=(EnvDTE._DTE)dte; app.MainWindow.Visible=false; app.SuppressUI=true; app.UserControl=false; }
    public OfflineBuildLease(int processId) {
        job=CreateJobObject(IntPtr.Zero,null);
        var info=new Extended(); info.BasicLimitInformation.LimitFlags=0x2000;
        if(job==IntPtr.Zero || !SetInformationJobObject(job,9,ref info,(uint)Marshal.SizeOf(info))) { Dispose(); throw new Exception("Cannot bound offline XAE process lifetime"); }
        using(var process=Process.GetProcessById(processId)) {
            if(!AssignProcessToJobObject(job,process.Handle)) { Dispose(); throw new Exception("Cannot contain owned offline XAE instance"); }
        }
    }
    public void Dispose() { if(job!=IntPtr.Zero) { CloseHandle(job); job=IntPtr.Zero; } }
}
'@
    $filter = [BuildMessageFilter]::new()
    $started = [DateTime]::UtcNow
    $existing = @([Diagnostics.Process]::GetProcesses() | ForEach-Object { $_.Id })
    $type = [Type]::GetTypeFromProgID('TcXaeShell.DTE.15.0', $true)
    $dte = [Activator]::CreateInstance($type)
    $ownerId = [OfflineBuildLease]::DteProcess($dte)
    if ($ownerId -le 0 -or $existing -contains $ownerId) { throw 'Automation returned an existing/unidentified instance; refusing to use it.' }
    $process = [Diagnostics.Process]::GetProcessById($ownerId)
    if ($process.StartTime.ToUniversalTime() -lt $started.AddSeconds(-2)) { throw 'Automation process ownership cannot be established.' }
    $owned = $true
    $lease = [OfflineBuildLease]::new($ownerId)
    [OfflineBuildLease]::Prepare($dte)
    $directory = [string]$request.directory
    $projectPath = [string]$request.projectPath
    $dte.Solution.Create($directory, 'OfflineBuild')
    $template = 'C:\TwinCAT\3.1\Components\Base\PrjTemplate\TwinCAT Project.tsproj'
    if (-not [IO.File]::Exists($template)) { throw 'Installed TwinCAT system project template missing.' }
    $systemProject = $dte.Solution.AddFromTemplate($template, (Join-Path $directory 'System'), 'OfflineSystem', $false)
    $manager = $systemProject.Object
    $plc = $manager.LookupTreeItem('TIPC')
    $null = $plc.CreateChild('', 2, '', $projectPath)
    $manager.ConfigurationManager.ActiveTargetPlatform = [string]$request.platform
    $dte.Solution.SaveAs((Join-Path $directory 'OfflineBuild.sln'))
    $dte.Solution.SolutionBuild.BuildProject(('Release|' + [string]$request.platform), $projectPath, $true)
    $buildInfo = [int]$dte.Solution.SolutionBuild.LastBuildInfo
    $items = [OfflineBuildLease]::Errors($dte)
    $artifacts = @(Get-ChildItem -LiteralPath $directory -Recurse -File -Filter '*.tmc' | Where-Object { $_.Length -gt 0 } | ForEach-Object { $_.FullName })
    $output = @{ compiled=($buildInfo -eq 0 -and $artifacts.Count -gt 0); lastBuildInfo=$buildInfo; diagnostics=$items.Items; tmcArtifacts=$artifacts;
                 diagnosticCount=[int]$items.Count; diagnosticsTruncated=($items.Count -gt 500);
                 platform=[string]$request.platform; xaeVersion=[string]$dte.Version;
                 projectPath=$projectPath; ownedProcessId=$ownerId; controllerAccessed=$false }
    [Console]::Out.WriteLine(($output | ConvertTo-Json -Depth 8 -Compress))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    [Console]::Error.WriteLine($_.ScriptStackTrace)
    exit 1
} finally {
    if ($owned -and $null -ne $dte) { try { $dte.Quit() } catch { } }
    if ($null -ne $lease) { $lease.Dispose() }
    if ($null -ne $dte) { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($dte) | Out-Null }
    if ($null -ne $filter) { $filter.Dispose() }
}
