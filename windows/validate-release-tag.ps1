[CmdletBinding()]
param(
    [string]$Tag = "",
    [string]$ExpectedSha = "",
    [string]$Remote = "origin",
    [switch]$AllowMissing,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Resolve-PresspeechTagCommit([string[]]$Lines, [string]$ReleaseTag) {
    $tagRef = "refs/tags/$ReleaseTag"
    $peeledRef = "$tagRef^{}"
    $refs = @{}
    foreach ($line in $Lines) {
        $parts = @($line -split "\s+", 2)
        if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[0-9a-f]{40}$') {
            throw "git returned malformed release-tag metadata"
        }
        if ($parts[1] -eq $tagRef -or $parts[1] -eq $peeledRef) {
            $refs[$parts[1]] = $parts[0]
        }
    }
    if ($refs.ContainsKey($peeledRef)) {
        return $refs[$peeledRef]
    }
    if ($refs.ContainsKey($tagRef)) {
        return $refs[$tagRef]
    }
    return $null
}

function Assert-PresspeechReleaseTag(
    [string[]]$Lines,
    [string]$ReleaseTag,
    [string]$ApprovedSha,
    [bool]$MissingAllowed
) {
    $actualSha = Resolve-PresspeechTagCommit $Lines $ReleaseTag
    if (-not $actualSha) {
        if ($MissingAllowed) {
            return $null
        }
        throw "Release tag $ReleaseTag does not exist"
    }
    if ($actualSha -ne $ApprovedSha) {
        throw "Release tag $ReleaseTag points to $actualSha, not approved commit $ApprovedSha"
    }
    return $actualSha
}

if ($SelfTest) {
    $approved = "a" * 40
    $tagObject = "b" * 40
    $lightweight = @("$approved`trefs/tags/windows-v1.2.3")
    $annotated = @(
        "$tagObject`trefs/tags/windows-v1.2.3",
        "$approved`trefs/tags/windows-v1.2.3^{}"
    )
    if ((Assert-PresspeechReleaseTag $lightweight "windows-v1.2.3" $approved $false) -ne $approved) {
        throw "lightweight release-tag self-test failed"
    }
    if ((Assert-PresspeechReleaseTag $annotated "windows-v1.2.3" $approved $false) -ne $approved) {
        throw "annotated release-tag self-test failed"
    }
    if ((Assert-PresspeechReleaseTag @() "windows-v1.2.3" $approved $true) -ne $null) {
        throw "missing release-tag self-test failed"
    }
    try {
        Assert-PresspeechReleaseTag $lightweight "windows-v1.2.3" ("c" * 40) $false
        throw "mismatched release-tag self-test did not reject the tag"
    } catch {
        if ($_.Exception.Message -notlike "Release tag * points to *") {
            throw
        }
    }
    Write-Output "Windows release-tag validation self-test passed."
    exit 0
}

if ($Tag -notmatch '^windows-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "Tag must have the canonical form windows-vX.Y.Z"
}
if ($ExpectedSha -notmatch '^[0-9a-f]{40}$') {
    throw "Expected SHA must be a lowercase 40-character Git commit ID"
}
if (-not $Remote) {
    throw "Git remote must not be empty"
}

$tagRef = "refs/tags/$Tag"
$remoteLines = @(& git ls-remote --exit-code $Remote $tagRef "$tagRef^{}")
$gitStatus = $LASTEXITCODE
if ($gitStatus -eq 2) {
    $remoteLines = @()
} elseif ($gitStatus -ne 0) {
    throw "Could not read release tag $Tag from Git remote $Remote"
}

$resolved = Assert-PresspeechReleaseTag $remoteLines $Tag $ExpectedSha $AllowMissing.IsPresent
if ($resolved) {
    Write-Output "Release tag $Tag points to approved commit $resolved."
} else {
    Write-Output "Release tag $Tag is available."
}
