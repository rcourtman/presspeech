[CmdletBinding()]
param(
    [string]$Version = "0.1.10",
    [string]$Python = (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    [string]$InnoCompiler = "",
    [string]$CertificatePath = $env:PRESSPEECH_CERT_PATH,
    [string]$CertificatePassword = $env:PRESSPEECH_CERT_PASSWORD,
    [string]$SignTool = "",
    [switch]$SkipInstaller,
    [switch]$ReusePackage
)

$ErrorActionPreference = "Stop"
$windowsRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$buildDir = Join-Path $windowsRoot "build"
$distDir = Join-Path $windowsRoot "dist"
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stageRoot = Join-Path $tempRoot "presspeech-package"
$stageBuildDir = Join-Path $stageRoot "build"
$stageDistDir = Join-Path $stageRoot "dist"

if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "Version must have the canonical form X.Y.Z"
}

# The installer name, PE metadata, GitHub tag, and the version used by the
# in-app updater must agree.
$expectedVersionLine = 'VERSION = "' + $Version + '"'
$declaredVersionLines = @(Get-Content -LiteralPath (Join-Path $windowsRoot "config.py") |
    Where-Object { $_ -match '^VERSION\s*=' })
if ($declaredVersionLines.Count -ne 1 -or
        $declaredVersionLines[0] -ne $expectedVersionLine) {
    throw "windows/config.py must declare $expectedVersionLine"
}

$Python = (Resolve-Path -LiteralPath $Python).Path
if ($SkipInstaller -and $ReusePackage) {
    throw "SkipInstaller and ReusePackage cannot be used together."
}
if (-not $ReusePackage) {
    & $Python (Join-Path $windowsRoot "release_requirements.py") --verify-environment
    if ($LASTEXITCODE -ne 0) {
        throw "Release Python environment does not match requirements-release.txt."
    }
}

if (-not $SkipInstaller -and -not $InnoCompiler) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $InnoCompiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $SkipInstaller -and
        (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler))) {
    throw "Inno Setup 6 was not found. Install JRSoftware.InnoSetup with winget."
}

if ($CertificatePath) {
    $CertificatePath = (Resolve-Path -LiteralPath $CertificatePath).Path
    if (-not $SignTool) {
        $SignTool = Get-ChildItem `
            -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $SignTool -or -not (Test-Path -LiteralPath $SignTool)) {
        throw "A signing certificate was supplied, but signtool.exe was not found."
    }
}

function Invoke-PresspeechSign([string]$Path) {
    if (-not $CertificatePath) {
        return
    }
    $arguments = @(
        "sign", "/fd", "SHA256", "/td", "SHA256",
        "/tr", "http://timestamp.digicert.com",
        "/f", $CertificatePath
    )
    if ($CertificatePassword) {
        $arguments += @("/p", $CertificatePassword)
    }
    $arguments += $Path
    & $SignTool @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed for $Path"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid") {
        throw "Signature verification failed for $Path`: $($signature.StatusMessage)"
    }
}

function Invoke-PresspeechPackageSelfTest([string]$Executable) {
    $resultPath = Join-Path $stageBuildDir "package-selftest.txt"
    Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
    $env:PRESSPEECH_PACKAGE_SELFTEST_RESULT = $resultPath
    try {
        $process = Start-Process `
            -FilePath $Executable `
            -ArgumentList "--package-selftest" `
            -WorkingDirectory (Split-Path -Parent $Executable) `
            -PassThru
        if (-not $process.WaitForExit(120000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Packaged executable self-test timed out."
        }
        if ($process.ExitCode -ne 0) {
            $detail = if (Test-Path -LiteralPath $resultPath) {
                (Get-Content -LiteralPath $resultPath -Raw).Trim()
            } else {
                "packaged executable did not reach the self-test entry point"
            }
            if ($detail -notmatch `
                    '^(packaged import unavailable: [A-Za-z0-9_.]+|packaged executable did not reach the self-test entry point)$') {
                $detail = "packaged executable returned an invalid self-test result"
            }
            throw "Packaged executable self-test failed: $detail"
        }
        if (-not (Test-Path -LiteralPath $resultPath) -or
                (Get-Content -LiteralPath $resultPath -Raw).Trim() -ne "ok") {
            throw "Packaged executable returned an invalid self-test result."
        }
    } finally {
        Remove-Item Env:PRESSPEECH_PACKAGE_SELFTEST_RESULT `
            -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
    }
    Write-Output "Packaged executable self-test passed."
}

if (-not $ReusePackage) {
foreach ($path in @($buildDir, $distDir)) {
    $fullPath = [IO.Path]::GetFullPath($path)
    if (-not $fullPath.StartsWith($windowsRoot + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to clean a path outside the Windows project: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}
$stageFullPath = [IO.Path]::GetFullPath($stageRoot)
if (-not $stageFullPath.StartsWith($tempRoot) -or
        [IO.Path]::GetFileName($stageFullPath) -ne "presspeech-package") {
    throw "Refusing to clean an unexpected staging path: $stageFullPath"
}
if (Test-Path -LiteralPath $stageFullPath) {
    Remove-Item -LiteralPath $stageFullPath -Recurse -Force
}
New-Item -ItemType Directory -Path $buildDir, $distDir, $stageBuildDir, $stageDistDir -Force | Out-Null

$parts = $Version.Split('.') | ForEach-Object { [int]$_ }
$versionFile = Join-Path $buildDir "version_info.txt"
$versionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($parts[0]), $($parts[1]), $($parts[2]), 0),
    prodvers=($($parts[0]), $($parts[1]), $($parts[2]), 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'rcourtman'),
        StringStruct('FileDescription', 'Presspeech local push-to-talk dictation'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', 'Presspeech'),
        StringStruct('OriginalFilename', 'Presspeech.exe'),
        StringStruct('ProductName', 'Presspeech'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
[IO.File]::WriteAllText($versionFile, $versionText, [Text.UTF8Encoding]::new($false))

$env:PRESSPEECH_VERSION_FILE = $versionFile
try {
    & $Python -m PyInstaller --noconfirm --clean `
        --workpath $stageBuildDir `
        --distpath $stageDistDir `
        (Join-Path $windowsRoot "Presspeech.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:PRESSPEECH_VERSION_FILE -ErrorAction SilentlyContinue
}
} else {
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
}

$appExe = Join-Path $stageDistDir "Presspeech\Presspeech.exe"
if (-not (Test-Path -LiteralPath $appExe)) {
    throw "Packaged executable was not created: $appExe"
}
Invoke-PresspeechSign $appExe
Invoke-PresspeechPackageSelfTest $appExe

$appSize = (Get-ChildItem -LiteralPath (Join-Path $stageDistDir "Presspeech") -Recurse -File |
    Measure-Object -Property Length -Sum).Sum
Write-Output "Packaged app: $([math]::Round($appSize / 1GB, 2)) GB"
Write-Output "Packaged app directory: $(Join-Path $stageDistDir 'Presspeech')"
if ($SkipInstaller) {
    return
}

$installerOutput = Join-Path $distDir "installer"
& $InnoCompiler `
    "/DAppVersion=$Version" `
    "/DSourceDir=$(Join-Path $stageDistDir 'Presspeech')" `
    "/DInstallerOutputDir=$installerOutput" `
    (Join-Path $windowsRoot "installer.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$installer = Join-Path $installerOutput "Presspeech-Setup-$Version-x64.exe"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Installer was not created: $installer"
}
Invoke-PresspeechSign $installer

$installerFile = Get-Item -LiteralPath $installer
$hash = Get-FileHash -LiteralPath $installer -Algorithm SHA256
$checksumFile = "$installer.sha256"
[IO.File]::WriteAllText(
    $checksumFile,
    "$($hash.Hash.ToLowerInvariant())  $($installerFile.Name)`n",
    [Text.UTF8Encoding]::new($false))
Write-Output "Installer: $($installerFile.FullName) ($([math]::Round($installerFile.Length / 1GB, 2)) GB)"
Write-Output "SHA256: $($hash.Hash)"
Write-Output "Checksum file: $checksumFile"
