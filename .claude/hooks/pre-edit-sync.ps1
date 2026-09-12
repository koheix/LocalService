# PreToolUse (Edit|Write|NotebookEdit) フック。
#
# 「PR がいつ承認されるか分からない」ため、origin は自分の知らないうちに進む。
# コードを編集する前に必ずリモートと同期し、ズレたまま作業を積み上げるのを防ぐ。
#
#   1. git リポジトリ外のファイル、または git 不在なら何もしない
#   2. 直近に同期済み（既定 10 分以内・同一ブランチ）ならスキップして高速に通す
#   3. main / master を直接編集しようとしたら拒否する
#   4. origin を fetch し、upstream より遅れていれば pull --ff-only する
#      作業ツリーが汚れていて fast-forward できないときは拒否して指示を出す
#   5. origin/main がブランチの分岐点より進んでいれば rebase を促す（拒否はしない）

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\Common.ps1')

# テストから上書きできるようにしておく（既定 600 秒）。
$throttleSeconds = 600
if ($env:LLM_CONSOLE_SYNC_THROTTLE_SECONDS) {
    $parsed = 0
    if ([int]::TryParse($env:LLM_CONSOLE_SYNC_THROTTLE_SECONDS, [ref]$parsed)) { $throttleSeconds = $parsed }
}

# 実在する祖先ディレクトリまで遡る（新規ファイルの作成では親がまだ無いことがある）。
function Resolve-ExistingDirectory {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    $current = $Path
    for ($i = 0; $i -lt 64; $i++) {
        if ([string]::IsNullOrWhiteSpace($current)) { return $null }
        if (Test-Path -LiteralPath $current -PathType Container) { return $current }
        $parent = Split-Path -Path $current -Parent
        if ($parent -eq $current) { return $null }
        $current = $parent
    }
    return $null
}

$hookInput = Read-HookInput
$filePath = Get-Prop $hookInput 'tool_input.file_path'
$cwd = Get-Prop $hookInput 'cwd' (Get-Location).Path

$gitExe = Get-GitExe
if (-not $gitExe) { exit 0 }

# 編集対象のディレクトリを起点にリポジトリを判定する。
$startDirectory = $null
if ($filePath) { $startDirectory = Resolve-ExistingDirectory (Split-Path -Path $filePath -Parent) }
if (-not $startDirectory) { $startDirectory = Resolve-ExistingDirectory $cwd }
if (-not $startDirectory) { exit 0 }

$repoRoot = Get-RepoRoot -GitExe $gitExe -Directory $startDirectory
if (-not $repoRoot) { exit 0 }

# 追跡対象外の作業ファイルへの書き込みでは同期しない（無駄な fetch を避ける）。
if ($filePath) {
    $normalized = $filePath -replace '/', '\'
    if ($normalized -like '*\.claude\state\*' -or $normalized -like '*\reports\*') { exit 0 }
}

$branch = Get-CurrentBranch -GitExe $gitExe -RepoRoot $repoRoot
if (-not $branch) { exit 0 }

if (Test-ProtectedBranch $branch) {
    Deny-Tool @"
$branch ブランチを直接編集することはできません（CLAUDE.md の規約）。

作業用のブランチを切ってからやり直してください:
    git switch -c feature/<機能名>     # 新機能
    git switch -c fix/<不具合名>       # バグ修正
    git switch -c chore/<作業名>       # 設定・ドキュメント

すでに $branch 上で編集してしまった変更がある場合は、そのままブランチを切れば変更は引き継がれます。
"@
}

# 直近に同期済みならスキップする。毎回の編集で fetch するのは遅すぎる。
$statePath = Get-StateFilePath -RepoRoot $repoRoot -Name 'last-sync.json'
$state = Read-StateFile -Path $statePath
if ($state) {
    $lastBranch = Get-Prop $state 'branch'
    $lastAt = Get-Prop $state 'syncedAtUtc'
    if ($lastBranch -eq $branch -and $lastAt) {
        try {
            $elapsed = ([DateTime]::UtcNow - [DateTime]::Parse($lastAt, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)).TotalSeconds
            if ($elapsed -ge 0 -and $elapsed -lt $throttleSeconds) { exit 0 }
        } catch {
            # 状態が読めなければ普通に同期する。
        }
    }
}

$remotes = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('remote')
$hasOrigin = $remotes.Ok -and (($remotes.StdOut -split '\r?\n') -contains 'origin')

if (-not $hasOrigin) {
    Write-StateFile -Path $statePath -Value @{ branch = $branch; syncedAtUtc = [DateTime]::UtcNow.ToString('o') }
    exit 0
}

$fetch = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('fetch', 'origin', '--prune', '--quiet')
if (-not $fetch.Ok) {
    # ネットワークが無いだけの可能性があるので、編集自体は止めずに知らせるだけにする。
    Write-Notice "origin への fetch に失敗しました。オフラインの可能性があります。リモートの最新状態を反映せずに作業しています。`n$($fetch.StdErr)"
    exit 0
}

$notices = New-Object System.Collections.Generic.List[string]

# upstream があり、遅れているなら取り込む。
$upstreamResult = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
if ($upstreamResult.Ok -and $upstreamResult.StdOut) {
    $upstream = $upstreamResult.StdOut
    $counts = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-list', '--left-right', '--count', "HEAD...$upstream")
    if ($counts.Ok -and $counts.StdOut -match '^\s*(\d+)\s+(\d+)\s*$') {
        $ahead = [int]$Matches[1]
        $behind = [int]$Matches[2]

        if ($behind -gt 0) {
            $status = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('status', '--porcelain')
            $isDirty = $status.Ok -and -not [string]::IsNullOrWhiteSpace($status.StdOut)

            if ($isDirty) {
                Deny-Tool @"
$upstream が $behind コミット進んでいますが、作業ツリーに未コミットの変更があるため取り込めません。

先に変更を退避または確定してから編集してください:
    git stash push -u -m "作業中"   # 退避する
    git pull --ff-only
    git stash pop
または:
    git add -A; git commit -m "..."
    git pull --rebase

未コミットの変更:
$($status.StdOut)
"@
            }

            $pull = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('pull', '--ff-only', '--quiet')
            if ($pull.Ok) {
                $notices.Add("$upstream から $behind コミットを取り込みました（pull --ff-only）。")
            } else {
                Deny-Tool @"
$upstream が $behind コミット進んでいますが、fast-forward で取り込めませんでした
（ローカルにも $ahead コミットあり、履歴が分岐しています）。

先に手動で解決してください:
    git pull --rebase

git の出力:
$($pull.StdErr)
"@
            }
        }
    }
}

# 分岐元（origin/main）が進んでいないか。進んでいれば rebase を促す（作業は止めない）。
$defaultRemoteBranch = $null
foreach ($candidate in @('origin/main', 'origin/master')) {
    $verify = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-parse', '--verify', '--quiet', $candidate)
    if ($verify.Ok -and $verify.StdOut) { $defaultRemoteBranch = $candidate; break }
}

if ($defaultRemoteBranch) {
    $mergeBase = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('merge-base', 'HEAD', $defaultRemoteBranch)
    if ($mergeBase.Ok -and $mergeBase.StdOut) {
        $drift = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-list', '--count', "$($mergeBase.StdOut)..$defaultRemoteBranch")
        if ($drift.Ok -and $drift.StdOut -match '^\d+$' -and [int]$drift.StdOut -gt 0) {
            $notices.Add("$defaultRemoteBranch が分岐点から $($drift.StdOut) コミット進んでいます（PR がマージされた可能性があります）。衝突を避けるため 'git rebase $defaultRemoteBranch' を検討してください。")
        }
    }
}

Write-StateFile -Path $statePath -Value @{ branch = $branch; syncedAtUtc = [DateTime]::UtcNow.ToString('o') }

if ($notices.Count -gt 0) {
    Write-Notice ("[origin 同期] " + ($notices -join ' / '))
}

exit 0
