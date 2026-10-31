[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$candidateArgument = $Installer
$versionArgument = $ExpectedVersion
# Import the existing refusal, registry and checked-process helpers. This
# performs only their pure self-tests before admitting the real installation.
. (Join-Path $PSScriptRoot 'smoke-installer.ps1') -SelfTest
$Installer = $candidateArgument
$ExpectedVersion = $versionArgument
Assert-DisposableRunner $IsWindows $env:GITHUB_ACTIONS $env:RUNNER_ENVIRONMENT
Assert-NoExistingInstall @(Get-ExistingInstallEvidence)
Assert-CanonicalVersion $ExpectedVersion
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
if ([IO.Path]::GetFileName($installerPath) -cne "Presspeech-Setup-$ExpectedVersion-x64.exe") {
    throw 'Candidate installer name does not match its version'
}
$candidateHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash

# Retain the fresh-install/startup/uninstall qualification as a prerequisite.
$baselineVerificationToken = $env:GH_TOKEN
$baselineActionsToken = $env:GITHUB_TOKEN
try {
    $env:GH_TOKEN = ''
    $env:GITHUB_TOKEN = ''
    & (Join-Path $PSScriptRoot 'smoke-installer.ps1') -Installer $installerPath -ExpectedVersion $ExpectedVersion
} finally {
    $env:GH_TOKEN = $baselineVerificationToken
    $env:GITHUB_TOKEN = $baselineActionsToken
}

$qaRoot = Join-Path $env:RUNNER_TEMP ('presspeech-full-upgrade-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $qaRoot | Out-Null
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\Presspeech'
$appExecutable = Join-Path $installDir 'Presspeech.exe'
$originalEnvironment = @{}
foreach ($name in @('APPDATA', 'LOCALAPPDATA', 'HF_HOME', 'HF_HUB_CACHE')) {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$uninstaller = $null
$results = @()
function Invoke-UpgradeProcess(
        [string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds,
        [string]$Description, [hashtable]$Environment = @{}) {
    # Baseline verification needs repository credentials; installed binaries
    # and uninstallers do not. Keep the verification token in the controller.
    $childEnvironment = @{ GH_TOKEN = ''; GITHUB_TOKEN = '' }
    foreach ($key in $Environment.Keys) { $childEnvironment[$key] = $Environment[$key] }
    Invoke-CheckedProcess $FilePath $Arguments $TimeoutSeconds $Description $childEnvironment
}
try {
    # Only the disposable child applications see these synthetic data roots.
    # The installer uses its real per-user Programs location on this runner,
    # so this also exercises default-directory legacy cleanup and long paths.
    $env:APPDATA = Join-Path $qaRoot 'roaming'
    $env:LOCALAPPDATA = Join-Path $qaRoot 'local'
    $env:HF_HOME = Join-Path $qaRoot 'huggingface'
    $env:HF_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
    $dataFiles = @(
        (Join-Path $env:APPDATA 'Presspeech\config.json'),
        (Join-Path $env:LOCALAPPDATA 'Presspeech\model-integrity\synthetic.json'),
        (Join-Path $env:HF_HUB_CACHE 'synthetic-model\keep.txt')
    )
    foreach ($file in $dataFiles) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $file) -Force | Out-Null
        $content = if ($file -ceq $dataFiles[0]) {
            '{"qualification_marker":"preserve-user-data","model":"base.en","model_explicit":true,"check_updates":false}'
        } else { '{"qualification_marker":"preserve-user-data"}' }
        [IO.File]::WriteAllText($file, $content)
    }
    $dataHashes = @{}
    foreach ($file in $dataFiles) { $dataHashes[$file] = (Get-FileHash -LiteralPath $file).Hash }
    $baselines = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installer-upgrade-baselines.json') -Raw | ConvertFrom-Json).baselines
    foreach ($baseline in $baselines) {
        if (Test-Path -LiteralPath $installDir) { throw 'Previous qualification left the install directory behind' }
        $baselineRoot = Join-Path $qaRoot $baseline.version
        New-Item -ItemType Directory -Path $baselineRoot | Out-Null
        $releaseText = gh api "repos/rcourtman/presspeech/releases/tags/$($baseline.tag)"
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read immutable baseline release' }
        $release = $releaseText | ConvertFrom-Json
        if (-not $release.immutable -or $release.draft) { throw 'Baseline is not an immutable published release' }
        $asset = @($release.assets | Where-Object { $_.name -ceq $baseline.asset })
        if ($asset.Count -ne 1 -or $asset[0].digest -cne "sha256:$($baseline.sha256)" -or
                $asset[0].size -ne $baseline.bytes) { throw 'Pinned baseline release metadata changed' }
        gh release verify $baseline.tag --repo rcourtman/presspeech
        if ($LASTEXITCODE -ne 0) { throw 'Baseline release attestation failed' }
        gh release download $baseline.tag --repo rcourtman/presspeech --pattern $baseline.asset --dir $baselineRoot
        if ($LASTEXITCODE -ne 0) { throw 'Baseline download failed' }
        $oldInstaller = Join-Path $baselineRoot $baseline.asset
        if ((Get-Item -LiteralPath $oldInstaller).Length -ne $baseline.bytes -or
                (Get-FileHash -LiteralPath $oldInstaller -Algorithm SHA256).Hash.ToLowerInvariant() -cne $baseline.sha256) {
            throw 'Baseline installer digest mismatch'
        }
        gh release verify-asset $baseline.tag $oldInstaller --repo rcourtman/presspeech
        if ($LASTEXITCODE -ne 0) { throw 'Baseline asset attestation failed' }
        $releaseText | Set-Content -LiteralPath (Join-Path $baselineRoot 'release.json')
        Invoke-UpgradeProcess $oldInstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
            '/NOICONS', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/TASKS=',
            "/DIR=$installDir", "/LOG=$(Join-Path $baselineRoot 'legacy-install.log')") 600 'Install immutable legacy baseline'
        $uninstaller = Join-Path $installDir 'unins000.exe'
        if ((Get-Item -LiteralPath $appExecutable).VersionInfo.ProductVersion -cne $baseline.version) {
            throw 'Legacy installation version mismatch'
        }
        $oldResult = Join-Path $baselineRoot 'old-package.txt'
        Invoke-UpgradeProcess $appExecutable @('--package-selftest') 180 'Legacy installed package self-test' `
            @{PRESSPEECH_PACKAGE_SELFTEST_RESULT=$oldResult}
        if ((Get-Content -LiteralPath $oldResult -Raw).Trim() -cne 'ok') { throw 'Legacy package did not self-test' }
        $runKey = Open-CurrentUserRunKey $true
        $startupCommand = Get-PackagedStartupCommand $appExecutable
        try { Set-OwnedStartupEntry $runKey $startupCommand }
        finally { $runKey.Dispose() }

        foreach ($phase in @('upgrade', 'identical-reinstall')) {
            $before = @{}
            if ($phase -ceq 'identical-reinstall') {
                Get-ChildItem -LiteralPath $installDir -Directory -Filter '_presspeech-*' | ForEach-Object {
                    Get-ChildItem -LiteralPath $_.FullName -Recurse -File | ForEach-Object {
                        $before[$_.FullName] = $_.LastWriteTimeUtc.Ticks
                    }
                }
            }
            Invoke-UpgradeProcess $installerPath @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                '/NOICONS', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/TASKS=',
                "/DIR=$installDir", "/LOG=$(Join-Path $baselineRoot "$phase.log")") 900 'Upgrade installed package'
            if ((Get-Item -LiteralPath $appExecutable).VersionInfo.ProductVersion -cne $ExpectedVersion) {
                throw 'Upgraded executable version mismatch'
            }
            $bundles = @(Get-ChildItem -LiteralPath $installDir -Directory -Filter '_presspeech-*')
            if ($bundles.Count -ne 1 -or (Test-Path -LiteralPath (Join-Path $installDir '_internal'))) {
                throw 'Legacy or superseded bundle was not cleaned from the default installation'
            }
            foreach ($file in $before.Keys) {
                if ((Get-Item -LiteralPath $file).LastWriteTimeUtc.Ticks -ne $before[$file]) {
                    throw 'Identical reinstall overwrote immutable bundle data'
                }
            }
            $resultFile = Join-Path $baselineRoot "$phase-package.txt"
            Invoke-UpgradeProcess $appExecutable @('--package-selftest') 180 'Upgraded installed package self-test' `
                @{PRESSPEECH_PACKAGE_SELFTEST_RESULT=$resultFile}
            if ((Get-Content -LiteralPath $resultFile -Raw).Trim() -cne 'ok') { throw 'Upgraded package did not self-test' }
            Invoke-UpgradeProcess $appExecutable @('--selftest') 600 'Installed CPU model and decoder self-test' `
                @{CUDA_VISIBLE_DEVICES='-1'}
            $runKey = Open-CurrentUserRunKey $false
            try {
                if ($null -eq $runKey -or $runKey.GetValue('Presspeech') -cne $startupCommand) {
                    throw 'Upgrade did not preserve the selected startup command'
                }
            } finally { if ($runKey) { $runKey.Dispose() } }
            foreach ($file in $dataFiles) {
                if ((Get-FileHash -LiteralPath $file).Hash -cne $dataHashes[$file]) { throw 'Upgrade changed user data' }
            }
        }
        Invoke-UpgradeProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            "/LOG=$(Join-Path $baselineRoot 'uninstall.log')") 300 'Uninstall upgraded package'
        for ($attempt = 0; $attempt -lt 120 -and (Test-Path -LiteralPath $installDir); $attempt++) {
            Start-Sleep -Milliseconds 250
        }
        if (Test-Path -LiteralPath $installDir) { throw 'Uninstall left application files behind' }
        $uninstaller = $null
        $runKey = Open-CurrentUserRunKey $false
        try { Assert-StartupEntryRemoved $runKey }
        finally { if ($runKey) { $runKey.Dispose() } }
        foreach ($file in $dataFiles) {
            if ((Get-FileHash -LiteralPath $file).Hash -cne $dataHashes[$file]) { throw 'Uninstall changed user data' }
        }
        $results += @{baseline=$baseline.tag; baseline_sha256=$baseline.sha256;
            candidate_sha256=$candidateHash.ToLowerInvariant(); upgrade='passed'; identical_reinstall='passed';
            installed_package_selftests='passed'; startup_preservation_and_cleanup='passed';
            installed_cpu_model_warmup_and_silence_decode='passed';
            user_data_preserved='passed'; uninstall='passed'}
        $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qaRoot 'results.json')
        Remove-Item -LiteralPath $oldInstaller -Force
        Write-Output "Qualified full installed upgrade from $($baseline.tag) to $ExpectedVersion."
    }
    if ((Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash -cne $candidateHash) {
        throw 'Candidate installer changed during qualification'
    }
} finally {
    try {
        if ($uninstaller -and (Test-Path -LiteralPath $uninstaller)) {
            Invoke-UpgradeProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') 300 'Qualification cleanup uninstall'
        }
    } finally {
        foreach ($name in $originalEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
        }
    }
}
