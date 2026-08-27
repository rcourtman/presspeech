[CmdletBinding()]
param(
    [string]$ReleaseJson = "",
    [string]$Tag = "",
    [string]$Version = "",
    [string]$ExpectedSha = "",
    [string]$Installer = "",
    [string]$Checksum = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Get-PresspeechReleaseDisposition(
    [string]$Json,
    [string]$ReleaseTag,
    [string]$ReleaseVersion,
    [string]$ApprovedSha,
    [string]$InstallerPath,
    [string]$ChecksumPath
) {
    if ($ReleaseVersion -notmatch `
            '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or
            $ReleaseTag -cne "windows-v$ReleaseVersion") {
        throw "Release version and tag must have the canonical Windows form"
    }
    if ($ApprovedSha -notmatch '^[0-9a-f]{40}$') {
        throw "Expected SHA must be a lowercase 40-character Git commit ID"
    }
    if (-not $Json) {
        return "create"
    }

    try {
        $release = $Json | ConvertFrom-Json
    } catch {
        throw "GitHub returned invalid release metadata"
    }
    if ($null -eq $release) {
        throw "GitHub returned invalid release metadata"
    }
    foreach ($property in @(
        "tag_name", "name", "target_commitish", "draft", "prerelease", "assets"
    )) {
        if ($property -notin $release.PSObject.Properties.Name) {
            throw "GitHub returned incomplete release metadata"
        }
    }

    $expectedName = "Presspeech for Windows $ReleaseVersion"
    if ($release.tag_name -cne $ReleaseTag -or
            $release.name -cne $expectedName -or
            $release.target_commitish -cne $ApprovedSha) {
        throw "Existing $ReleaseTag release does not match approved metadata"
    }
    if ($release.draft -isnot [bool] -or
            $release.prerelease -isnot [bool]) {
        throw "GitHub returned invalid release state metadata"
    }
    if (-not $release.prerelease) {
        throw "Existing $ReleaseTag release is not a Windows prerelease"
    }
    if (-not $release.draft) {
        return "verify-published"
    }

    $installerFile = Get-Item -LiteralPath $InstallerPath
    $checksumFile = Get-Item -LiteralPath $ChecksumPath
    $expectedInstallerName = "Presspeech-Setup-$ReleaseVersion-x64.exe"
    $expectedChecksumName = "$expectedInstallerName.sha256"
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
    if ($checksumText -cne "$installerDigest  $expectedInstallerName`n") {
        throw "Local checksum does not exactly bind the release installer"
    }

    $expectedAssets = [System.Collections.Generic.Dictionary[string,object]]::new(
        [System.StringComparer]::Ordinal)
    $expectedAssets.Add(
        $expectedInstallerName,
        @{
            Size = $installerFile.Length
            Digest = $installerDigest
        })
    $expectedAssets.Add(
        $expectedChecksumName,
        @{
            Size = $checksumFile.Length
            Digest = $checksumDigest
        })
    $assetsByName = [System.Collections.Generic.Dictionary[string,object]]::new(
        [System.StringComparer]::Ordinal)
    foreach ($asset in @($release.assets)) {
        $assetName = [string]$asset.name
        if (-not $assetName -or $assetsByName.ContainsKey($assetName)) {
            throw "$ReleaseTag contains missing or duplicate draft asset names"
        }
        if (-not $expectedAssets.ContainsKey($assetName)) {
            throw "$ReleaseTag contains unexpected draft asset $assetName"
        }
        $assetsByName.Add($assetName, $asset)
    }
    foreach ($assetName in $assetsByName.Keys) {
        $asset = $assetsByName[$assetName]
        $expected = $expectedAssets[$assetName]
        if ($asset.state -cne "uploaded" -or
                [long]$asset.size -ne $expected.Size -or
                $asset.digest -cne "sha256:$($expected.Digest)") {
            throw "$assetName does not match the locally verified draft asset"
        }
        $expectedUrl = (
            "https://github.com/rcourtman/presspeech/releases/download/" +
            "$ReleaseTag/$assetName")
        if ($asset.browser_download_url -cne $expectedUrl) {
            throw "$assetName has an unexpected draft download URL"
        }
    }

    $hasInstaller = $assetsByName.ContainsKey($expectedInstallerName)
    $hasChecksum = $assetsByName.ContainsKey($expectedChecksumName)
    if ($hasInstaller -and $hasChecksum) {
        return "publish-draft"
    }
    if ($hasInstaller) {
        return "upload-checksum"
    }
    if ($hasChecksum) {
        return "upload-installer"
    }
    return "upload-both"
}

function Assert-PresspeechRejected([scriptblock]$Action, [string]$Message) {
    try {
        & $Action
        throw "invalid release-state self-test was accepted"
    } catch {
        if ($_.Exception.Message -notlike $Message) {
            throw
        }
    }
}

if ($SelfTest) {
    $selfTestRoot = Join-Path `
        ([IO.Path]::GetTempPath()) `
        ("presspeech-release-state-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $selfTestRoot | Out-Null
    try {
        $version = "1.2.3"
        $tag = "windows-v$version"
        $approved = "a" * 40
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
        $installerAsset = [pscustomobject]@{
            name = $installerName
            state = "uploaded"
            size = (Get-Item -LiteralPath $installerPath).Length
            digest = "sha256:$installerDigest"
            browser_download_url = (
                "https://github.com/rcourtman/presspeech/releases/" +
                "download/$tag/$installerName")
        }
        $checksumAsset = [pscustomobject]@{
            name = "$installerName.sha256"
            state = "uploaded"
            size = (Get-Item -LiteralPath $checksumPath).Length
            digest = "sha256:$checksumDigest"
            browser_download_url = (
                "https://github.com/rcourtman/presspeech/releases/" +
                "download/$tag/$installerName.sha256")
        }
        $release = [pscustomobject]@{
            tag_name = $tag
            name = "Presspeech for Windows $version"
            target_commitish = $approved
            draft = $true
            prerelease = $true
            assets = @()
        }
        $emptyDraftJson = $release | ConvertTo-Json -Depth 4 -Compress
        if ((Get-PresspeechReleaseDisposition `
                "" $tag $version $approved $installerPath $checksumPath) -cne
                "create") {
            throw "missing release-state self-test returned the wrong disposition"
        }
        if ((Get-PresspeechReleaseDisposition `
                $emptyDraftJson $tag $version $approved `
                $installerPath $checksumPath) -cne "upload-both") {
            throw "empty draft self-test returned the wrong disposition"
        }
        $release.assets = @($installerAsset)
        $installerDraftJson = $release | ConvertTo-Json -Depth 4 -Compress
        if ((Get-PresspeechReleaseDisposition `
                $installerDraftJson $tag $version $approved `
                $installerPath $checksumPath) -cne "upload-checksum") {
            throw "installer draft self-test returned the wrong disposition"
        }
        $release.assets = @($checksumAsset)
        if ((Get-PresspeechReleaseDisposition `
                ($release | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $approved $installerPath $checksumPath) -cne
                "upload-installer") {
            throw "checksum draft self-test returned the wrong disposition"
        }
        $release.assets = @($installerAsset, $checksumAsset)
        $completeDraftJson = $release | ConvertTo-Json -Depth 4 -Compress
        if ((Get-PresspeechReleaseDisposition `
                $completeDraftJson $tag $version $approved `
                $installerPath $checksumPath) -cne "publish-draft") {
            throw "complete draft self-test returned the wrong disposition"
        }
        $release.draft = $false
        if ((Get-PresspeechReleaseDisposition `
                ($release | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $approved $installerPath $checksumPath) -cne
                "verify-published") {
            throw "published release self-test returned the wrong disposition"
        }

        foreach ($testCase in @(
            @{ Property = "tag_name"; Value = "windows-v1.2.4"; Message = "Existing * does not match*" },
            @{ Property = "name"; Value = "Unexpected"; Message = "Existing * does not match*" },
            @{ Property = "target_commitish"; Value = ("b" * 40); Message = "Existing * does not match*" },
            @{ Property = "prerelease"; Value = $false; Message = "Existing * is not a Windows prerelease" },
            @{ Property = "draft"; Value = "false"; Message = "GitHub returned invalid release state metadata" }
        )) {
            $invalid = $emptyDraftJson | ConvertFrom-Json
            $invalid.($testCase.Property) = $testCase.Value
            Assert-PresspeechRejected {
                Get-PresspeechReleaseDisposition `
                    ($invalid | ConvertTo-Json -Depth 4 -Compress) `
                    $tag $version $approved $installerPath $checksumPath
            } $testCase.Message
        }

        $invalidAsset = $completeDraftJson | ConvertFrom-Json
        $invalidAsset.assets[0].digest = "sha256:$('0' * 64)"
        Assert-PresspeechRejected {
            Get-PresspeechReleaseDisposition `
                ($invalidAsset | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $approved $installerPath $checksumPath
        } "*does not match the locally verified draft asset"
        $unexpectedAsset = $completeDraftJson | ConvertFrom-Json
        $unexpectedAsset.assets[0].name = "unexpected.exe"
        Assert-PresspeechRejected {
            Get-PresspeechReleaseDisposition `
                ($unexpectedAsset | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $approved $installerPath $checksumPath
        } "*contains unexpected draft asset*"

        $incomplete = $emptyDraftJson | ConvertFrom-Json
        $incomplete.PSObject.Properties.Remove("name")
        Assert-PresspeechRejected {
            Get-PresspeechReleaseDisposition `
                ($incomplete | ConvertTo-Json -Depth 4 -Compress) `
                $tag $version $approved $installerPath $checksumPath
        } "GitHub returned incomplete release metadata"
        Assert-PresspeechRejected {
            Get-PresspeechReleaseDisposition `
                "not-json" $tag $version $approved $installerPath $checksumPath
        } "GitHub returned invalid release metadata"
    } finally {
        Remove-Item -LiteralPath $selfTestRoot -Recurse -Force
    }
    Write-Output "Windows release-state validation self-test passed."
    exit 0
}

Get-PresspeechReleaseDisposition `
    $ReleaseJson $Tag $Version $ExpectedSha $Installer $Checksum
