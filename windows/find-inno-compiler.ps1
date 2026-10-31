function Get-PresspeechInnoCompilerVersion([string]$Compiler) {
    # Ask the loaded engine itself. PE version resources on the command-line
    # front end or engine can be placeholders and are not the compiler API.
    # Even /O- can clean an output directory, so give the probe its own empty
    # directory and never use the checkout or the package staging directory.
    $probeRoot = Join-Path ([IO.Path]::GetTempPath()) ('presspeech-inno-probe-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($probeRoot) | Out-Null
    $process = [Diagnostics.Process]::new()
    try {
        $process.StartInfo.FileName = $Compiler
        $process.StartInfo.Arguments = '/O- -'
        $process.StartInfo.WorkingDirectory = $probeRoot
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardInput = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        if (-not $process.Start()) { throw 'Compiler version probe did not start' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.WriteLine("[Setup]`nAppName=Compiler version check`nAppVersion=0`nDefaultDirName={tmp}\CompilerVersionCheck`nUninstallable=no`nOutput=no`nOutputDir=.")
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Compiler version probe timed out'
        }
        $output = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0 -or
                $output -notmatch '(?m)^Compiler engine version: .+? (\d+\.\d+\.\d+)\r?$') {
            throw 'Compiler did not report its engine version'
        }
        return $Matches[1]
    } finally {
        $process.Dispose()
        Remove-Item -LiteralPath $probeRoot -Recurse -Force
    }
}

function Find-PresspeechInnoCompiler([string]$ExplicitPath = '') {
    # Hosted images can retain an older compiler beside winget's installation.
    # Selecting the first existing path does not enforce the release pin.
    $candidates = if ($ExplicitPath) { @($ExplicitPath) } else { @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) }
    $observed = @()
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try { $version = Get-PresspeechInnoCompilerVersion $candidate }
        catch { $observed += "$candidate (version probe failed)"; continue }
        $observed += "$candidate (engine $version)"
        if ($version -ceq '6.7.3') {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Inno Setup 6.7.3 compiler engine was not found. Install the pinned JRSoftware.InnoSetup version or pass its ISCC.exe path. Observed: $($observed -join '; ')"
}
