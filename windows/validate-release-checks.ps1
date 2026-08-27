[CmdletBinding()]
param(
    [string]$CheckWorkflowJson = "",
    [string]$WindowsWorkflowJson = "",
    [string]$ExpectedSha = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Assert-PresspeechWorkflowSucceeded(
    [string]$Json,
    [string]$ApprovedSha,
    [string]$WorkflowPath,
    [string]$WorkflowLabel
) {
    try {
        $payload = $Json | ConvertFrom-Json
    } catch {
        throw "GitHub returned invalid $WorkflowLabel workflow metadata"
    }
    if ($null -eq $payload -or $null -eq $payload.workflow_runs) {
        throw "GitHub returned invalid $WorkflowLabel workflow metadata"
    }

    $matchingRuns = @($payload.workflow_runs | Where-Object {
        $_.head_sha -ceq $ApprovedSha -and
        $_.head_branch -ceq "main" -and
        $_.event -ceq "push" -and
        $_.path -ceq $WorkflowPath
    })
    if ($matchingRuns.Count -eq 0) {
        throw "$WorkflowLabel workflow has not run for approved commit $ApprovedSha"
    }

    # A commit can acquire another run if main is moved away and back. Bind the
    # release to the newest run rather than letting an older success mask it.
    try {
        $latest = $matchingRuns |
            Sort-Object { [long]$_.id } -Descending |
            Select-Object -First 1
    } catch {
        throw "GitHub returned invalid $WorkflowLabel workflow metadata"
    }
    if ($latest.status -cne "completed") {
        throw "$WorkflowLabel workflow is not complete for approved commit $ApprovedSha"
    }
    if ($latest.conclusion -cne "success") {
        throw "$WorkflowLabel workflow did not succeed for approved commit $ApprovedSha"
    }
    return [long]$latest.id
}

function Assert-PresspeechRejected([scriptblock]$Action, [string]$Message) {
    try {
        & $Action
        throw "invalid release-check self-test was accepted"
    } catch {
        if ($_.Exception.Message -notlike $Message) {
            throw
        }
    }
}

if ($SelfTest) {
    $approved = "a" * 40
    $workflowPath = ".github/workflows/windows.yml"
    $goodRun = [pscustomobject]@{
        id = 100
        path = $workflowPath
        event = "push"
        status = "completed"
        conclusion = "success"
        head_sha = $approved
        head_branch = "main"
    }
    $goodJson = [pscustomobject]@{
        total_count = 1
        workflow_runs = @($goodRun)
    } | ConvertTo-Json -Depth 4 -Compress
    if ((Assert-PresspeechWorkflowSucceeded `
            $goodJson $approved $workflowPath "Windows CI") -ne 100) {
        throw "successful release-check self-test returned the wrong run"
    }

    $olderSuccess = $goodJson | ConvertFrom-Json
    $newerFailure = $goodJson | ConvertFrom-Json
    $newerFailure.workflow_runs[0].id = 101
    $newerFailure.workflow_runs[0].conclusion = "failure"
    $mixedJson = [pscustomobject]@{
        total_count = 2
        workflow_runs = @(
            $olderSuccess.workflow_runs[0],
            $newerFailure.workflow_runs[0]
        )
    } | ConvertTo-Json -Depth 4 -Compress
    Assert-PresspeechRejected {
        Assert-PresspeechWorkflowSucceeded `
            $mixedJson $approved $workflowPath "Windows CI"
    } "Windows CI workflow did not succeed*"

    $pending = $goodJson | ConvertFrom-Json
    $pending.workflow_runs[0].status = "in_progress"
    $pending.workflow_runs[0].conclusion = $null
    Assert-PresspeechRejected {
        Assert-PresspeechWorkflowSucceeded `
            ($pending | ConvertTo-Json -Depth 4 -Compress) `
            $approved $workflowPath "Windows CI"
    } "Windows CI workflow is not complete*"

    foreach ($mismatch in @(
        @{ Property = "head_sha"; Value = ("b" * 40) },
        @{ Property = "head_branch"; Value = "feature" },
        @{ Property = "event"; Value = "pull_request" },
        @{ Property = "path"; Value = ".github/workflows/other.yml" }
    )) {
        $wrongRun = $goodJson | ConvertFrom-Json
        $wrongRun.workflow_runs[0].($mismatch.Property) = $mismatch.Value
        Assert-PresspeechRejected {
            Assert-PresspeechWorkflowSucceeded `
                ($wrongRun | ConvertTo-Json -Depth 4 -Compress) `
                $approved $workflowPath "Windows CI"
        } "Windows CI workflow has not run*"
    }

    Assert-PresspeechRejected {
        Assert-PresspeechWorkflowSucceeded `
            "not-json" $approved $workflowPath "Windows CI"
    } "GitHub returned invalid Windows CI workflow metadata"

    Write-Output "Windows release-check validation self-test passed."
    exit 0
}

if ($ExpectedSha -notmatch '^[0-9a-f]{40}$') {
    throw "Expected SHA must be a lowercase 40-character Git commit ID"
}

$checkRun = Assert-PresspeechWorkflowSucceeded `
    $CheckWorkflowJson `
    $ExpectedSha `
    ".github/workflows/check.yml" `
    "Repository CI"
$windowsRun = Assert-PresspeechWorkflowSucceeded `
    $WindowsWorkflowJson `
    $ExpectedSha `
    ".github/workflows/windows.yml" `
    "Windows CI"
Write-Output (
    "Approved commit $ExpectedSha passed repository CI run $checkRun " +
    "and Windows CI run $windowsRun.")
