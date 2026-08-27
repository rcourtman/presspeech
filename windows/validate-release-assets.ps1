[CmdletBinding()]
param(
    [string]$ReleaseJson = "",
    [string]$Tag = "",
    [string]$Version = "",
    [string]$Installer = "",
    [string]$Checksum = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Assert-PresspeechPublishedAssets(
    [object]$Release,
    [string]$ReleaseTag,
    [string]$InstallerName,
    [long]$InstallerSize,
    [string]$InstallerDigest,
    [string]$ChecksumName,
    [long]$ChecksumSize,
    [string]$ChecksumDigest
) {
    if ($Release.tag_name -cne $ReleaseTag -or $Release.draft -or
            -not $Release.prerelease) {
        throw "Published release metadata does not describe $ReleaseTag prerelease"
    }

    $assets = @($Release.assets)
    if ($assets.Count -ne 2) {
        throw "$ReleaseTag must publish exactly the installer and checksum assets"
    }
    $assetsByName = [System.Collections.Generic.Dictionary[string,object]]::new(
        [System.StringComparer]::Ordinal)
    foreach ($asset in $assets) {
        $name = [string]$asset.name
        if (-not $name -or $assetsByName.ContainsKey($name)) {
            throw "$ReleaseTag contains missing or duplicate asset names"
        }
        $assetsByName.Add($name, $asset)
    }

    foreach ($expected in @(
        @{
            Name = $InstallerName
            Size = $InstallerSize
            Digest = $InstallerDigest
        },
        @{
            Name = $ChecksumName
            Size = $ChecksumSize
            Digest = $ChecksumDigest
        }
    )) {
        if (-not $assetsByName.ContainsKey($expected.Name)) {
            throw "$ReleaseTag is missing asset $($expected.Name)"
        }
        $asset = $assetsByName[$expected.Name]
        if ($asset.state -cne "uploaded") {
            throw "$($expected.Name) is not fully uploaded"
        }
        if ([long]$asset.size -ne $expected.Size) {
            throw "$($expected.Name) has unexpected published size"
        }
        if ($asset.digest -cne "sha256:$($expected.Digest)") {
            throw "$($expected.Name) has unexpected published SHA-256"
        }
        $expectedUrl = (
            "https://github.com/rcourtman/presspeech/releases/download/" +
            "$ReleaseTag/$($expected.Name)")
        if ($asset.browser_download_url -cne $expectedUrl) {
            throw "$($expected.Name) has an unexpected download URL"
        }
    }
}

function Test-PresspeechPublishedAssets(
    [string]$Json,
    [string]$ReleaseTag,
    [string]$ReleaseVersion,
    [string]$InstallerPath,
    [string]$ChecksumPath
) {
    if ($ReleaseVersion -notmatch `
            '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or
            $ReleaseTag -cne "windows-v$ReleaseVersion") {
        throw "Release version and tag must have the canonical Windows form"
    }

    $expectedInstallerName = "Presspeech-Setup-$ReleaseVersion-x64.exe"
    $expectedChecksumName = "$expectedInstallerName.sha256"
    $installerFile = Get-Item -LiteralPath $InstallerPath
    $checksumFile = Get-Item -LiteralPath $ChecksumPath
    if ($installerFile.Name -cne $expectedInstallerName -or
            $checksumFile.Name -cne $expectedChecksumName) {
        throw "Local release assets do not have the expected versioned names"
    }

    $installerDigest = (
        Get-FileHash -LiteralPath $installerFile.FullName -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $checksumDigest = (
        Get-FileHash -LiteralPath $checksumFile.FullName -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $checksumText = [IO.File]::ReadAllText($checksumFile.FullName)
    $expectedChecksumText = "$installerDigest  $expectedInstallerName`n"
    if ($checksumText -cne $expectedChecksumText) {
        throw "Local checksum does not exactly bind the published installer"
    }

    try {
        $release = $Json | ConvertFrom-Json
    } catch {
        throw "GitHub returned invalid release metadata"
    }
    Assert-PresspeechPublishedAssets `
        $release `
        $ReleaseTag `
        $expectedInstallerName `
        $installerFile.Length `
        $installerDigest `
        $expectedChecksumName `
        $checksumFile.Length `
        $checksumDigest
}

function Assert-PresspeechRejected([scriptblock]$Action, [string]$Message) {
    try {
        & $Action
        throw "invalid release-asset self-test was accepted"
    } catch {
        if ($_.Exception.Message -notlike $Message) {
            throw
        }
    }
}

if ($SelfTest) {
    $selfTestRoot = Join-Path `
        ([IO.Path]::GetTempPath()) `
        ("presspeech-release-assets-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $selfTestRoot | Out-Null
    try {
        $version = "1.2.3"
        $tag = "windows-v$version"
        $installerName = "Presspeech-Setup-$version-x64.exe"
        $installerPath = Join-Path $selfTestRoot $installerName
        $checksumPath = "$installerPath.sha256"
        [IO.File]::WriteAllBytes($installerPath, [byte[]](1, 2, 3, 4))
        $installerDigest = (
            Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText(
            $checksumPath,
            "$installerDigest  $installerName`n",
            [Text.UTF8Encoding]::new($false))
        $checksumDigest = (
            Get-FileHash -LiteralPath $checksumPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        $release = [pscustomobject]@{
            tag_name = $tag
            draft = $false
            prerelease = $true
            assets = @(
                [pscustomobject]@{
                    name = $installerName
                    state = "uploaded"
                    size = (Get-Item -LiteralPath $installerPath).Length
                    digest = "sha256:$installerDigest"
                    browser_download_url = (
                        "https://github.com/rcourtman/presspeech/releases/" +
                        "download/$tag/$installerName")
                },
                [pscustomobject]@{
                    name = "$installerName.sha256"
                    state = "uploaded"
                    size = (Get-Item -LiteralPath $checksumPath).Length
                    digest = "sha256:$checksumDigest"
                    browser_download_url = (
                        "https://github.com/rcourtman/presspeech/releases/" +
                        "download/$tag/$installerName.sha256")
                }
            )
        }
        $validJson = $release | ConvertTo-Json -Depth 4 -Compress
        Test-PresspeechPublishedAssets `
            $validJson $tag $version $installerPath $checksumPath

        $badDigest = $validJson | ConvertFrom-Json
        $badDigest.assets[0].digest = "sha256:$('0' * 64)"
        Assert-PresspeechRejected {
            Test-PresspeechPublishedAssets `
                ($badDigest | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $installerPath $checksumPath
        } "*unexpected published SHA-256"

        $badUrl = $validJson | ConvertFrom-Json
        $badUrl.assets[0].browser_download_url = "https://example.com/installer.exe"
        Assert-PresspeechRejected {
            Test-PresspeechPublishedAssets `
                ($badUrl | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $installerPath $checksumPath
        } "*unexpected download URL"

        $extraAsset = $validJson | ConvertFrom-Json
        $extraAsset.assets += [pscustomobject]@{
            name = "unexpected.txt"
            state = "uploaded"
            size = 1
            digest = "sha256:$('0' * 64)"
            browser_download_url = "https://github.com/unexpected.txt"
        }
        Assert-PresspeechRejected {
            Test-PresspeechPublishedAssets `
                ($extraAsset | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $installerPath $checksumPath
        } "*exactly the installer and checksum assets"

        [IO.File]::WriteAllText(
            $checksumPath,
            "$installerDigest *$installerName`n",
            [Text.UTF8Encoding]::new($false))
        Assert-PresspeechRejected {
            Test-PresspeechPublishedAssets `
                $validJson $tag $version $installerPath $checksumPath
        } "Local checksum does not exactly bind*"
    } finally {
        Remove-Item -LiteralPath $selfTestRoot -Recurse -Force
    }
    Write-Output "Windows published release-asset validation self-test passed."
    exit 0
}

Test-PresspeechPublishedAssets `
    $ReleaseJson $Tag $Version $Installer $Checksum
Write-Output "Published $Tag assets match the locally verified release files."
