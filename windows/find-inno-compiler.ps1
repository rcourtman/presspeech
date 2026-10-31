function Find-PresspeechInnoCompiler([string]$ExplicitPath = '') {
    # Hosted images can retain an older compiler beside winget's installation.
    # Selecting the first existing path does not enforce the release pin.
    $candidates = if ($ExplicitPath) { @($ExplicitPath) } else { @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($candidate)
        if ($version.FileMajorPart -eq 6 -and $version.FileMinorPart -eq 7 -and
                $version.FileBuildPart -eq 3) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw 'Inno Setup 6.7.3 compiler was not found. Install the pinned JRSoftware.InnoSetup version or pass its ISCC.exe path.'
}
