[CmdletBinding()]
param(
    [string]$Installer = "",
    [string]$ExpectedVersion = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
# /DIR does not isolate Inno Setup's AppId or uninstall registration.
# Real installation is restricted to a disposable hosted CI machine.
$uninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\{33F4F983-1C0C-4E1C-9706-C4B693043E81}_is1"

function Assert-CanonicalVersion([string]$Version) {
    if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z') {
        throw "ExpectedVersion must have the canonical form X.Y.Z"
    }
}

function Assert-DisposableRunner([bool]$WindowsHost, [string]$Actions, [string]$Runner) {
    if (-not $WindowsHost -or $Actions -cne "true" -or $Runner -cne "github-hosted") {
        throw "Installer qualification requires a disposable GitHub-hosted Windows runner; use -SelfTest elsewhere."
    }
}

function Get-ExistingInstallEvidence {
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
            $registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
            try {
                $key = $registry.OpenSubKey($uninstallKey, $false)
                if ($null -ne $key) {
                    $key.Dispose()
                    "existing uninstall registration"
                }
                $runKey = $registry.OpenSubKey("Software\Microsoft\Windows\CurrentVersion\Run", $false)
                if ($null -ne $runKey) {
                    try {
                        if ($runKey.GetValueNames() -contains "Presspeech") { "existing startup entry" }
                    } finally { $runKey.Dispose() }
                }
            } finally { $registry.Dispose() }
        }
    }
    if (Get-Process -Name Presspeech -ErrorAction SilentlyContinue) { "running Presspeech process" }
    if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA "Programs\Presspeech")) {
        "existing default installation directory"
    }
}

function Assert-NoExistingInstall([string[]]$Evidence) {
    if ($Evidence.Count -gt 0) {
        throw "Installer qualification refused: $($Evidence -join ', ')."
    }
}

function Get-PackagedStartupCommand([string]$Executable) {
    # Matches app._autostart_command(..., frozen=True): quote the absolute
    # executable only, including when the hosted runner path contains spaces.
    if (-not $Executable -or $Executable.IndexOfAny([char[]]@('"', "`r", "`n")) -ge 0) {
        throw "Invalid installed executable path for startup qualification"
    }
    return '"' + [IO.Path]::GetFullPath($Executable) + '"'
}

function Set-OwnedStartupEntry($Key, [string]$Command) {
    # Recheck immediately before writing; never overwrite even an empty value.
    if ($null -eq $Key) { throw "Current-user Run key is unavailable" }
    if ($Key.GetValueNames() -contains "Presspeech") {
        throw "Startup qualification refused to overwrite an existing Presspeech entry"
    }
    $Key.SetValue("Presspeech", $Command, [Microsoft.Win32.RegistryValueKind]::String)
    if ($Key.GetValueNames() -notcontains "Presspeech" -or
            $Key.GetValueKind("Presspeech") -ne [Microsoft.Win32.RegistryValueKind]::String -or
            $Key.GetValue("Presspeech") -cne $Command) {
        throw "Startup qualification entry did not round-trip exactly"
    }
}

function Assert-StartupEntryRemoved($Key) {
    if ($null -ne $Key -and $Key.GetValueNames() -contains "Presspeech") {
        throw "Uninstaller left the Presspeech startup entry behind"
    }
}

function Open-RunSubKey($Registry, [bool]$Writable) {
    $path = "Software\Microsoft\Windows\CurrentVersion\Run"
    $key = $Registry.OpenSubKey($path, $Writable)
    if ($null -eq $key -and $Writable) {
        # A fresh disposable image may not have this parent yet. The sole
        # write caller runs only after all host/install/package guards pass.
        $key = $Registry.CreateSubKey($path)
    }
    return $key
}

function Open-CurrentUserRunKey([bool]$Writable) {
    # The packaged executable and installer are x64. Match the app's default
    # HKCU view. Read-only inspection never creates the parent key.
    $registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
        [Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]::Registry64)
    try {
        return Open-RunSubKey $registry $Writable
    } finally { $registry.Dispose() }
}

function Invoke-CheckedProcess(
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [string]$Description,
        [hashtable]$Environment = @{}) {
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    foreach ($argument in $Arguments) {
        $startInfo.ArgumentList.Add($argument)
    }
    foreach ($key in $Environment.Keys) { $startInfo.Environment[$key] = $Environment[$key] }
    $process = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "$Description did not start"
    }
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            $null = $process.WaitForExit(5000)
            throw "$Description timed out"
        }
        if ($process.ExitCode -ne 0) {
            throw "$Description failed with exit code $($process.ExitCode)"
        }
    } finally {
        $process.Dispose()
    }
}

function Invoke-SelfTest {
    # This exercises helpers only: no installer, registry write, application
    # launch or model download on developer machines or self-hosted workers.
    function Assert-Rejected([scriptblock]$Action, [string]$Message) {
        try { & $Action } catch {
            if ($_.Exception.Message -like $Message) { return }
            throw
        }
        throw "Self-test did not reject: $Message"
    }
    Assert-CanonicalVersion "0.1.12"
    foreach ($invalid in @("", "1", "1.2", "01.2.3", "1.02.3", "1.2.03", "1.2.3`n")) {
        Assert-Rejected { Assert-CanonicalVersion $invalid } "ExpectedVersion must*"
    }
    Assert-DisposableRunner $true "true" "github-hosted"
    foreach ($runner in @("", "self-hosted")) {
        Assert-Rejected { Assert-DisposableRunner $true "true" $runner } "Installer qualification requires*"
    }
    Assert-Rejected { Assert-DisposableRunner $false "true" "github-hosted" } "Installer qualification requires*"
    Assert-Rejected { Assert-DisposableRunner $true "false" "github-hosted" } "Installer qualification requires*"
    Assert-NoExistingInstall @()
    foreach ($evidence in @("existing uninstall registration", "existing startup entry", "running Presspeech process", "existing default installation directory")) {
        Assert-Rejected { Assert-NoExistingInstall @($evidence) } "Installer qualification refused*"
    }
    # An in-memory registry double checks ownership and failed-write behavior
    # without ever opening the registry, including on Windows -SelfTest runs.
    function New-TestRunKey {
        $key = [pscustomobject]@{ Values = @{}; Kinds = @{}; Writes = 0; DropWrite = $false; WrongKind = $false; WrongValue = $false }
        $key | Add-Member ScriptMethod GetValueNames { return @($this.Values.Keys) }
        $key | Add-Member ScriptMethod GetValue { param($Name) return $this.Values[$Name] }
        $key | Add-Member ScriptMethod GetValueKind { param($Name) return $this.Kinds[$Name] }
        $key | Add-Member ScriptMethod SetValue {
            param($Name, $Value, $Kind)
            $this.Writes++
            if ($this.DropWrite) { return }
            $this.Values[$Name] = if ($this.WrongValue) { "unexpected" } else { $Value }
            $this.Kinds[$Name] = if ($this.WrongKind) { [Microsoft.Win32.RegistryValueKind]::ExpandString } else { $Kind }
        }
        return $key
    }
    $registry = [pscustomobject]@{ Key = $null; Creates = 0; LastWritable = $null }
    $registry | Add-Member ScriptMethod OpenSubKey {
        param($Path, $Writable)
        if ($Path -cne "Software\Microsoft\Windows\CurrentVersion\Run") { throw "Unexpected registry parent" }
        $this.LastWritable = $Writable
        return $this.Key
    }
    $registry | Add-Member ScriptMethod CreateSubKey {
        param($Path)
        if ($Path -cne "Software\Microsoft\Windows\CurrentVersion\Run") { throw "Unexpected registry parent" }
        $this.Creates++
        $this.Key = New-TestRunKey
        return $this.Key
    }
    if ($null -ne (Open-RunSubKey $registry $false) -or $registry.Creates -ne 0 -or $registry.LastWritable) {
        throw "Read-only inspection created or opened a writable registry key"
    }
    $createdRunKey = Open-RunSubKey $registry $true
    if ($null -eq $createdRunKey -or $registry.Creates -ne 1 -or -not $registry.LastWritable) {
        throw "Writable startup qualification did not create the missing parent"
    }
    if (-not [object]::ReferenceEquals($createdRunKey, (Open-RunSubKey $registry $true)) -or $registry.Creates -ne 1) {
        throw "Writable startup qualification recreated an existing parent"
    }
    if (-not [object]::ReferenceEquals($createdRunKey, (Open-RunSubKey $registry $false)) -or $registry.Creates -ne 1 -or $registry.LastWritable) {
        throw "Read-only inspection changed an existing parent"
    }
    $command = Get-PackagedStartupCommand (Join-Path ([IO.Path]::GetTempPath()) "run with spaces/Presspeech.exe")
    if ($command -cne ('"' + [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) "run with spaces/Presspeech.exe")) + '"')) {
        throw "Packaged startup command was not quoted exactly"
    }
    foreach ($invalidPath in @("", 'bad"path', "bad`npath", "bad`rpath")) {
        Assert-Rejected { Get-PackagedStartupCommand $invalidPath } "Invalid installed executable path*"
    }
    $runKey = New-TestRunKey
    $runKey.Values["Unrelated"] = "preserve me"
    Set-OwnedStartupEntry $runKey $command
    if ($runKey.Writes -ne 1 -or $runKey.Values["Unrelated"] -cne "preserve me") {
        throw "Startup qualification wrote beyond its owned value"
    }
    Assert-Rejected { Assert-StartupEntryRemoved $runKey } "Uninstaller left the Presspeech startup entry behind"
    $runKey.Values.Remove("Presspeech")
    Assert-StartupEntryRemoved $runKey
    Assert-StartupEntryRemoved $null
    Assert-Rejected { Set-OwnedStartupEntry $null $command } "Current-user Run key is unavailable"
    foreach ($existing in @("", "other command")) {
        $runKey = New-TestRunKey
        $runKey.Values["pReSsPeEcH"] = $existing
        Assert-Rejected { Set-OwnedStartupEntry $runKey $command } "Startup qualification refused to overwrite*"
        if ($runKey.Writes -ne 0 -or $runKey.Values["pReSsPeEcH"] -cne $existing) {
            throw "Startup qualification overwrote an existing value"
        }
    }
    foreach ($fault in @("DropWrite", "WrongKind", "WrongValue")) {
        $runKey = New-TestRunKey
        $runKey.$fault = $true
        Assert-Rejected { Set-OwnedStartupEntry $runKey $command } "Startup qualification entry did not round-trip exactly"
    }
    $shell = (Get-Process -Id $PID).Path
    Invoke-CheckedProcess $shell @("-NoProfile", "-NonInteractive", "-Command", "exit 0") 10 "Child success"
    Assert-Rejected {
        Invoke-CheckedProcess $shell @("-NoProfile", "-NonInteractive", "-Command", "exit 7") 10 "Child failure"
    } "Child failure failed with exit code 7"
    Assert-Rejected {
        Invoke-CheckedProcess $shell @("-NoProfile", "-NonInteractive", "-Command", "Start-Sleep 30") 1 "Child timeout"
    } "Child timeout timed out"
    $original = $env:PRESSPEECH_PACKAGE_SELFTEST_RESULT
    Invoke-CheckedProcess $shell @("-NoProfile", "-NonInteractive", "-Command",
        'if ($env:PRESSPEECH_PACKAGE_SELFTEST_RESULT -cne "path with spaces") { exit 8 }') 10 "Child environment" `
        @{ PRESSPEECH_PACKAGE_SELFTEST_RESULT = "path with spaces" }
    if ($env:PRESSPEECH_PACKAGE_SELFTEST_RESULT -cne $original) { throw "Self-test changed the parent environment" }
    Write-Output "Installer smoke self-test passed."
}

if ($SelfTest) {
    Invoke-SelfTest
    return
}

Assert-DisposableRunner $IsWindows $env:GITHUB_ACTIONS $env:RUNNER_ENVIRONMENT
Assert-NoExistingInstall @(Get-ExistingInstallEvidence)
Assert-CanonicalVersion $ExpectedVersion
if (-not $Installer) {
    throw "Installer is required"
}
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$expectedName = "Presspeech-Setup-$ExpectedVersion-x64.exe"
if ([IO.Path]::GetFileName($installerPath) -cne $expectedName) {
    throw "Installer must be named $expectedName"
}

$installerDigest = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
if (-not $env:RUNNER_TEMP -or -not (Test-Path -LiteralPath $env:RUNNER_TEMP -PathType Container)) {
    throw "Hosted runner temporary directory is unavailable"
}
$tempRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP)
$smokeRoot = Join-Path $tempRoot ("presspeech-installer-smoke-" + [guid]::NewGuid())
$installDir = Join-Path $smokeRoot "app"
$installLog = Join-Path $smokeRoot "install.log"
$uninstallLog = Join-Path $smokeRoot "uninstall.log"
$cleanupLog = Join-Path $smokeRoot "cleanup-uninstall.log"
$resultPath = Join-Path $smokeRoot "package-selftest.txt"
$uninstaller = $null
$startupEntryAttempted = $false
$startupEntrySeeded = $false
$startupRemovalVerified = $false

try {
    New-Item -ItemType Directory -Path $smokeRoot -Force | Out-Null
    # build-release.ps1 stages under GetTempPath(), which need not equal
    # RUNNER_TEMP. The finished installer no longer needs this 4+ GB tree.
    $packageStage = Join-Path ([IO.Path]::GetTempPath()) "presspeech-package"
    if (Test-Path -LiteralPath $packageStage) {
        Remove-Item -LiteralPath $packageStage -Recurse -Force
    }
    Invoke-CheckedProcess `
        -FilePath $installerPath `
        -Arguments @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS",
            "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/RESTARTEXITCODE=3010",
            "/TASKS=", "/DIR=$installDir", "/LOG=$installLog") `
        -TimeoutSeconds 300 `
        -Description "Installer smoke install"

    $appExecutable = Join-Path $installDir "Presspeech.exe"
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "Installer did not install Presspeech.exe"
    }
    $productVersion = (Get-Item -LiteralPath $appExecutable).VersionInfo.ProductVersion
    if ($productVersion -cne $ExpectedVersion) {
        throw "Installed product version is '$productVersion', expected '$ExpectedVersion'"
    }

    $installedEvidence = @(Get-ExistingInstallEvidence)
    if ($installedEvidence -notcontains "existing uninstall registration") {
        throw "Installer did not register its uninstaller"
    }
    if ($installedEvidence -contains "existing startup entry") {
        throw "Silent installation unexpectedly enabled startup"
    }

    $uninstallers = @(Get-ChildItem -LiteralPath $installDir -Filter "unins*.exe" -File)
    if ($uninstallers.Count -ne 1) {
        throw "Installer did not create exactly one uninstaller"
    }
    $uninstaller = $uninstallers[0].FullName

    Invoke-CheckedProcess `
        -FilePath $appExecutable `
        -Arguments @("--package-selftest") `
        -TimeoutSeconds 120 `
        -Description "Installed executable self-test" `
        -Environment @{ PRESSPEECH_PACKAGE_SELFTEST_RESULT = $resultPath }
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf) -or
            (Get-Content -LiteralPath $resultPath -Raw).Trim() -cne "ok") {
        throw "Installed executable returned an invalid self-test result"
    }

    # Simulate only the startup value that the installed frozen app would
    # create after user opt-in. No GUI or settings file needs to be touched.
    # Every disposable-runner/install/package refusal above precedes this write.
    $startupCommand = Get-PackagedStartupCommand $appExecutable
    $runKey = Open-CurrentUserRunKey $true
    try {
        $startupEntryAttempted = $true
        Set-OwnedStartupEntry $runKey $startupCommand
        $startupEntrySeeded = $true
    } finally { if ($null -ne $runKey) { $runKey.Dispose() } }

    Invoke-CheckedProcess `
        -FilePath $uninstaller `
        -Arguments @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/LOG=$uninstallLog") `
        -TimeoutSeconds 300 `
        -Description "Installer smoke uninstall"

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path -LiteralPath $installDir) -and
            [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath $installDir) {
        throw "Uninstaller left the installation directory behind"
    }
    $runKey = Open-CurrentUserRunKey $false
    try { Assert-StartupEntryRemoved $runKey }
    finally { if ($null -ne $runKey) { $runKey.Dispose() } }
    $startupRemovalVerified = $true
    $uninstaller = $null
    Assert-NoExistingInstall @(Get-ExistingInstallEvidence)
    if ((Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash -cne $installerDigest) {
        throw "Installer changed during qualification"
    }
    @{
        schema_version = 1
        version = $ExpectedVersion
        installer_sha256 = $installerDigest.ToLowerInvariant()
        installed_package_selftest = "passed"
        startup_entry_roundtrip = "passed"
        startup_entry_uninstall_cleanup = "passed"
        uninstall = "passed"
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $smokeRoot "qualification.json")
    Write-Output "Qualified installer SHA-256: $installerDigest"
    Write-Output "Installer smoke test passed for Presspeech $ExpectedVersion."
} finally {
    if ($uninstaller -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        try {
            Invoke-CheckedProcess `
                -FilePath $uninstaller `
                -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$cleanupLog") `
                -TimeoutSeconds 300 `
                -Description "Installer smoke cleanup"
        } catch {
            Write-Warning "Installer smoke cleanup could not run the uninstaller."
        }
    }
    if ($startupEntryAttempted) {
        $startupState = "inspection-failed"
        try {
            $runKey = Open-CurrentUserRunKey $false
            try {
                $startupState = if ($null -ne $runKey -and $runKey.GetValueNames() -contains "Presspeech") { "present" } else { "absent" }
            } finally { if ($null -ne $runKey) { $runKey.Dispose() } }
        } catch { Write-Warning "Could not inspect startup entry after cleanup." }
        try {
            @{
                startup_entry_seeded = $startupEntrySeeded
                primary_uninstall_cleanup_verified = $startupRemovalVerified
                after_finally_cleanup = $startupState
            } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $smokeRoot "startup-cleanup.json")
        } catch { Write-Warning "Could not write startup cleanup evidence." }
        # Never manually remove the entry: that would conceal an uninstaller
        # regression. A failed run retains evidence on this disposable runner.
    }
    # Keep logs and any failed installation for inspection on this disposable
    # runner. Never delete failure evidence or hide an unsuccessful uninstall.
    Write-Output "Installer qualification evidence: $smokeRoot"
}
