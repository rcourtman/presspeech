[CmdletBinding()]
param(
    [string]$DispatchRef = "",
    [string]$ExpectedSha = "",
    [string]$HeadSha = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Assert-PresspeechReleaseContext(
    [string]$ReleaseRef,
    [string]$ApprovedSha,
    [string]$CheckedOutSha
) {
    if ($ReleaseRef -ne "refs/heads/main") {
        throw "Windows releases must be dispatched from the main branch"
    }
    if ($ApprovedSha -notmatch '^[0-9a-f]{40}$') {
        throw "Expected SHA must be a lowercase 40-character Git commit ID"
    }
    if ($CheckedOutSha -notmatch '^[0-9a-f]{40}$') {
        throw "Checked-out SHA must be a lowercase 40-character Git commit ID"
    }
    if ($ApprovedSha -ne $CheckedOutSha) {
        throw "Release ref moved: expected $ApprovedSha but workflow checked out $CheckedOutSha"
    }
    return $CheckedOutSha
}

if ($SelfTest) {
    $approved = "a" * 40
    if ((Assert-PresspeechReleaseContext "refs/heads/main" $approved $approved) -ne $approved) {
        throw "valid release-context self-test failed"
    }
    foreach ($badRef in @("refs/heads/feature", "refs/tags/windows-v1.2.3", "")) {
        try {
            Assert-PresspeechReleaseContext $badRef $approved $approved
            throw "non-main release-context self-test accepted $badRef"
        } catch {
            if ($_.Exception.Message -ne "Windows releases must be dispatched from the main branch") {
                throw
            }
        }
    }
    foreach ($testCase in @(
        @{ Expected = "ABC"; Head = $approved; Message = "Expected SHA must be*" },
        @{ Expected = $approved; Head = "ABC"; Message = "Checked-out SHA must be*" },
        @{ Expected = $approved; Head = ("b" * 40); Message = "Release ref moved:*" }
    )) {
        try {
            Assert-PresspeechReleaseContext `
                "refs/heads/main" $testCase.Expected $testCase.Head
            throw "invalid release-context self-test accepted bad metadata"
        } catch {
            if ($_.Exception.Message -notlike $testCase.Message) {
                throw
            }
        }
    }
    Write-Output "Windows release-context validation self-test passed."
    exit 0
}

Assert-PresspeechReleaseContext $DispatchRef $ExpectedSha $HeadSha | Out-Null
Write-Output "Release dispatch targets approved main commit $HeadSha."
