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
        # ISCC.exe is a reusable front end; the versioned compiler engine it
        # loads is ISCmplr.dll in the same directory. The front end's PE
        # version can remain unchanged across engine releases.
        $engine = Join-Path (Split-Path -Parent $candidate) 'ISCmplr.dll'
        if (-not (Test-Path -LiteralPath $engine -PathType Leaf)) { continue }
        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($engine)
        $observed += "$candidate (engine $($version.FileVersion))"
        if ($version.FileMajorPart -eq 6 -and $version.FileMinorPart -eq 7 -and
                $version.FileBuildPart -eq 3) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Inno Setup 6.7.3 compiler engine was not found. Install the pinned JRSoftware.InnoSetup version or pass its ISCC.exe path. Observed: $($observed -join '; ')"
}
