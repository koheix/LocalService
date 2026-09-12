# PreToolUse (Bash|PowerShell) フック。
#
# 人間が決めることを AI に決めさせない。ループを回す前提では、規約に「マージは人間が
# やる」と書いてあるだけでは足りない。取り返しのつかない一手だけを機械的に塞ぐ。
#
#   - PR のマージ・承認レビュー
#   - Issue の直接クローズ
#   - gh api での書き込み全般（上記を REST / GraphQL で迂回させない）
#   - gh alias で別名を作って検査をすり抜けること
#
# あわせて「止まる場所」を1つに決める。ループは PR を作った時点で止まり、その先へは
# 進まない。そのため gh pr create は、検証が済んでいなければ通さない。
#
#   - reports/verification.json  いまの HEAD でテストが全緑（make test-report が書く）
#   - reports/review-record.json いまの HEAD をレビュー済み（scripts/record_review.py が書く）
#
# 検証を飛ばす逃げ道は用意しない。以前は合図のファイルを置く方式にしていたが、
# そのファイルは AI 自身が作れるので、守りとして成立しなかった。落ちたまま PR を
# 出す必要があるなら、利用者が Claude Code の外で実行する。
#
# コマンドの解釈は lib\Common.ps1 の前処理を通す。説明文（メッセージ引数と
# ヒアドキュメント本文）だけを落とし、引用符は記号だけ外す。中身ごと落とすと
# `sh -c "gh pr merge 12"` のように引用符へ入れるだけで検査から消せてしまう。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\Common.ps1')

function Read-JsonFile {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    } catch {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    try {
        return (ConvertFrom-Json -InputObject $raw)
    } catch {
        return $null
    }
}

# 記録の数値を安全に読む。数値として読めない値は「失敗あり」に倒す（fail-closed）。
# [int] へ直接キャストすると StrictMode + ErrorActionPreference=Stop で例外になり、
# フックが非0で死ぬ。PreToolUse の非0終了はブロックにならないので、
# 壊れた記録を置くだけで検証を素通しできてしまう。
function Get-FailureCount {
    param($Record)

    $value = Get-Prop $Record 'failed' $null
    if ($null -eq $value) { return 1 }

    $parsed = 0
    if ([int]::TryParse([string]$value, [ref]$parsed)) { return $parsed }
    return 1
}

$hookInput = Read-HookInput
# 文字列以外（配列など）が来ても型変換で例外死しないようにする。
# フックが非0で終わるとブロックにならないため、死ぬこと自体が素通りになる。
$command = [string](Get-Prop $hookInput 'tool_input.command' '')
if ([string]::IsNullOrWhiteSpace($command)) { exit 0 }

$normalized = ConvertTo-BareCommand -Text (ConvertTo-InspectableCommand $command) -Name 'gh'
if ($normalized -notmatch '(^|[\s;&|(])gh(\.exe)?\s') { exit 0 }

$invocations = @(Get-CommandInvocation -CommandText $normalized -Name 'gh' -ValueOptions @('-R', '--repo'))
if ($invocations.Count -eq 0) { exit 0 }

foreach ($invocation in $invocations) {
    $subcommand = $invocation.Subcommand
    $arguments = @($invocation.Arguments)
    # サブコマンドの間にフラグを挟まれても取り違えないよう、Action は共通実装が決める。
    $action = $invocation.Action

    if ($subcommand -eq 'pr' -and $action -eq 'merge') {
        Deny-Tool @"
PR のマージは人間が行います（CLAUDE.md の必須ルール）。

PR の URL を利用者に伝えて、そこで止まってください。
"@
    }

    if ($subcommand -eq 'pr' -and $action -eq 'review') {
        if (($arguments -contains '--approve') -or ($arguments -contains '-a')) {
            Deny-Tool @"
PR の承認は人間が行います。

指摘があるなら --comment で伝えるか、レビュー結果を本文に書いてください。
"@
        }
    }

    if ($subcommand -eq 'issue' -and $action -eq 'close') {
        Deny-Tool @"
Issue を直接閉じないでください。

PR 本文に Closes #<番号> を書けば、マージされたときに自動で閉じます。
"@
    }

    # 別名を作れば、上の判定はすべてすり抜けられる（gh alias set m 'pr merge' → gh m 12）。
    if ($subcommand -eq 'alias' -and $action -eq 'set') {
        Deny-Tool @"
gh alias で別名を作らないでください。

このフックはサブコマンド名で判定しているため、別名を作ると
マージや承認の禁止をすり抜けられます。
"@
    }

    # gh api は「危険なエンドポイントを並べて塞ぐ」方式にしない。
    # 列挙は必ず漏れる（merge だけ塞いで approve・close・PR作成が空いていた）。
    # 書き込みかどうかで判定し、書き込みは一律で拒否する。
    #
    #   書き込みの印: -X/--method に GET 以外、あるいは本体を渡すフラグがある
    #   graphql はクエリ本文がどこにあっても読み切れないので、常に書き込み扱い
    #
    # 読み取り（GET）は通す。レビュー中に PR や Issue を読むのは通常の作業なので、
    # ここを塞ぐと pr-reviewer が動かなくなる。
    if ($subcommand -eq 'api') {
        # フラグと値がくっついた形（-XPUT、-ftitle=t）も見る。
        # 完全一致だけだと -XPUT repos/o/n/pulls/12/merge で素通りする。
        $writeMethod = $false
        for ($i = 0; $i -lt $arguments.Count; $i += 1) {
            $argument = $arguments[$i]

            # メソッド指定。-X PUT / -XPUT / --method PUT / --method=PUT
            $method = $null
            if ($argument -in @('-X', '--method')) {
                $method = if ($i + 1 -lt $arguments.Count) { $arguments[$i + 1] } else { '' }
            } elseif ($argument -match '^-X(.+)$') {
                $method = $Matches[1]
            } elseif ($argument -match '^--method=(.*)$') {
                $method = $Matches[1]
            }
            if ($null -ne $method) {
                if ($method.ToUpperInvariant() -ne 'GET') { $writeMethod = $true }
                continue
            }

            # 本体を渡すフラグ。これがあれば gh は POST する。
            # -f k=v / -fk=v / -F k=v / --field=k=v のいずれも拾う。
            if ($argument -in @('-f', '-F', '--field', '--raw-field', '--input')) { $writeMethod = $true }
            if ($argument -match '^-[fF].') { $writeMethod = $true }
            if ($argument -match '^(--field|--raw-field|--input)=') { $writeMethod = $true }
        }
        if ($action -eq 'graphql') { $writeMethod = $true }

        if ($writeMethod) {
            Deny-Tool @"
gh api で書き込み（GET 以外・フィールド付き・graphql）は使えません。

マージ・承認・Issue のクローズ・PR の作成は、いずれも人間の判断か、
検証を通した gh pr create を経由します。API で迂回しないでください。

読み取りだけなら通ります（例: gh api repos/o/n/pulls/12）。
"@
        }
    }

    if ($subcommand -ne 'pr' -or $action -ne 'create') { continue }

    # --- ここから gh pr create の検証ゲート ---
    #
    # リポジトリを特定できないときは通さない（fail-closed）。特定できないなら
    # 検証記録も特定できないので、「分からないから許可」は筋が通らない。

    $gitExe = Get-GitExe
    if (-not $gitExe) {
        Deny-Tool 'git が見つからないため、検証記録を確認できません。PR を作れません。'
    }

    $cwd = Get-Prop $hookInput 'cwd' (Get-Location).Path
    $repoRoot = Get-RepoRoot -GitExe $gitExe -Directory $cwd
    if (-not $repoRoot) {
        Deny-Tool 'git リポジトリの外なので、検証記録を確認できません。PR を作れません。'
    }

    $head = Invoke-Git -GitExe $gitExe -RepoRoot $repoRoot -GitArgs @('rev-parse', 'HEAD')
    if (-not $head.Ok -or [string]::IsNullOrWhiteSpace($head.StdOut)) {
        Deny-Tool 'HEAD を特定できないため、検証記録を確認できません。PR を作れません。'
    }
    $headSha = $head.StdOut.Trim()

    $verification = Read-JsonFile (Join-Path $repoRoot 'reports\verification.json')
    if ($null -eq $verification) {
        Deny-Tool @"
テスト結果が記録されていないため PR を作れません。

  make test-report

を実行してから、もう一度 PR を作ってください。
"@
    }

    $verifiedCommit = [string](Get-Prop $verification 'commit' '')
    if ($verifiedCommit -ne $headSha) {
        Deny-Tool @"
テスト結果が今のコミットのものではありません。

  記録: $verifiedCommit
  HEAD: $headSha

コミットのあとに make test-report を実行し直してください。
"@
    }

    $failed = Get-FailureCount $verification
    if ($failed -ne 0) {
        Deny-Tool @"
テストが $failed 件失敗しています（記録が壊れている場合も失敗として扱います）。
落ちたまま PR を作らないでください。

原因を直してから make test-report を実行し直してください。
テストを緩めたり消したりして通すのは禁止です。

どうしても落ちたまま出す必要があるなら、利用者自身が Claude Code の外で
gh pr create を実行してください。ここに逃げ道は用意しません。
"@
    }

    $review = Read-JsonFile (Join-Path $repoRoot 'reports\review-record.json')
    if ($null -eq $review) {
        Deny-Tool @"
差分のレビューが記録されていないため PR を作れません。

test-auditor（テストの監査）と pr-reviewer（差分のレビュー）を実行し、
その結果を次のコマンドで記録してください。

  python scripts/record_review.py --reviewers test-auditor,pr-reviewer --verdict pass
"@
    }

    $reviewedCommit = [string](Get-Prop $review 'commit' '')
    if ($reviewedCommit -ne $headSha) {
        Deny-Tool @"
レビューの記録が今のコミットのものではありません。

  記録: $reviewedCommit
  HEAD: $headSha

コミットのあとにレビューをやり直し、記録し直してください。
"@
    }

    $verdict = [string](Get-Prop $review 'verdict' '')
    if ($verdict -ne 'pass') {
        Deny-Tool @"
レビューの結果が pass ではありません（$verdict）。

指摘を直してからレビューをやり直してください。
「指摘は受けたが今回はこのまま」は無しです。
"@
    }
}

exit 0
