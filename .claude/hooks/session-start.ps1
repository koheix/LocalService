# SessionStart フック。
#
# セッションの開始時点で「いま origin がどうなっているか」を一度だけ確認して知らせる。
# 前回のセッションの後に PR がマージされているかもしれないので、
# 何も知らないまま作業を始めないようにする。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\Common.ps1')

$hookInput = Read-HookInput
$cwd = Get-Prop $hookInput 'cwd' (Get-Location).Path

$gitExe = Get-GitExe
if (-not $gitExe) {
    Write-Notice -HookEventName 'SessionStart' -Message 'git が見つかりません。ブランチ・PR 運用ができない状態です。'
    exit 0
}

$repoRoot = Get-RepoRoot -GitExe $gitExe -Directory $cwd
if (-not $repoRoot) { exit 0 }

$lines = New-Object System.Collections.Generic.List[string]

$branch = Get-CurrentBranch -GitExe $gitExe -RepoRoot $repoRoot
$lines.Add("現在のブランチ: $branch")

if (Test-ProtectedBranch $branch) {
    $lines.Add("※ $branch にいます。編集の前に必ず作業ブランチを切ってください（フックが編集を拒否します）。")
}

$status = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('status', '--porcelain')
if ($status.Ok -and -not [string]::IsNullOrWhiteSpace($status.StdOut)) {
    $changed = ($status.StdOut -split '\r?\n').Count
    $lines.Add("未コミットの変更: $changed ファイル")
}

$remotes = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('remote')
$hasOrigin = $remotes.Ok -and (($remotes.StdOut -split '\r?\n') -contains 'origin')

if ($hasOrigin) {
    $fetch = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('fetch', 'origin', '--prune', '--quiet')
    if ($fetch.Ok) {
        # セッション開始時に fetch 済みなので、直後の編集で pre-edit-sync が再度 fetch しないよう記録する。
        Write-StateFile -Path (Get-StateFilePath -RepoRoot $repoRoot -Name 'last-sync.json') `
            -Value @{ branch = $branch; syncedAtUtc = [DateTime]::UtcNow.ToString('o') }

        $defaultRemoteBranch = $null
        foreach ($candidate in @('origin/main', 'origin/master')) {
            $verify = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-parse', '--verify', '--quiet', $candidate)
            if ($verify.Ok -and $verify.StdOut) { $defaultRemoteBranch = $candidate; break }
        }

        if ($defaultRemoteBranch -and -not (Test-ProtectedBranch $branch)) {
            $mergeBase = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('merge-base', 'HEAD', $defaultRemoteBranch)
            if ($mergeBase.Ok -and $mergeBase.StdOut) {
                $drift = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-list', '--count', "$($mergeBase.StdOut)..$defaultRemoteBranch")
                if ($drift.Ok -and $drift.StdOut -match '^\d+$' -and [int]$drift.StdOut -gt 0) {
                    $lines.Add("$defaultRemoteBranch が分岐点から $($drift.StdOut) コミット進んでいます。/sync-main で取り込みを検討してください。")
                }
            }
        }
    } else {
        $lines.Add('origin への fetch に失敗しました（オフラインの可能性があります）。')
    }
}

# オープン中の PR。gh が無い・未認証なら黙ってスキップする。
$ghExe = Get-GhExe
if ($ghExe) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        Push-Location -LiteralPath $repoRoot
        $prJson = & $ghExe pr list --state open --limit 20 --json number,title,headRefName,isDraft 2>$null | Out-String
        $prExit = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $previous
    }

    if ($prExit -eq 0 -and -not [string]::IsNullOrWhiteSpace($prJson)) {
        try {
            $prs = ConvertFrom-Json $prJson
            if ($prs -and @($prs).Count -gt 0) {
                $lines.Add("マージ待ちの PR が $(@($prs).Count) 件あります:")
                foreach ($pr in @($prs)) {
                    $draft = ''
                    if ($pr.isDraft) { $draft = ' [下書き]' }
                    $lines.Add("  #$($pr.number) $($pr.title) ($($pr.headRefName))$draft")
                }
            }
        } catch {
            # PR 情報が取れなくてもセッション開始は妨げない。
        }
    }

    # 未対応の Issue。開発の起点になるので、PR と同じく開始時に見えるようにする。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        Push-Location -LiteralPath $repoRoot
        $issueJson = & $ghExe issue list --state open --limit 20 --json number,title,labels 2>$null | Out-String
        $issueExit = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $previous
    }

    if ($issueExit -eq 0 -and -not [string]::IsNullOrWhiteSpace($issueJson)) {
        try {
            $issues = ConvertFrom-Json $issueJson
            if ($issues -and @($issues).Count -gt 0) {
                $lines.Add("未対応の Issue が $(@($issues).Count) 件あります（/github-issue-development <番号> で着手）:")
                foreach ($issue in @($issues)) {
                    $labelText = ''
                    if ($issue.labels -and @($issue.labels).Count -gt 0) {
                        $labelText = ' [' + (($issue.labels | ForEach-Object { $_.name }) -join ', ') + ']'
                    }
                    $lines.Add("  #$($issue.number) $($issue.title)$labelText")
                }
            }
        } catch {
            # Issue 情報が取れなくてもセッション開始は妨げない。
        }
    }
}

if ($lines.Count -gt 0) {
    Write-HookJson @{
        hookSpecificOutput = @{
            hookEventName     = 'SessionStart'
            additionalContext = ("[リポジトリの状況]`n" + ($lines -join "`n"))
        }
    }
}

exit 0
