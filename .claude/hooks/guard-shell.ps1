# PreToolUse (Bash|PowerShell) フック。
#
# ブランチ運用と履歴を壊す操作を止める。規約は破られうるので、
# 取り返しのつかない操作だけは機械的に塞いでおく。
#
#   - main / master への直接コミット
#   - main / master への push
#   - --force-with-lease を伴わない強制 push
#   - 保護ブランチの削除
#
# コマンド文字列を単純に検索すると、コミットメッセージの本文に含まれる
# 「git push --force」のような文字列まで実コマンドと誤認してしまう。
# lib\Common.ps1 の前処理で説明文（メッセージ引数とヒアドキュメント本文）だけを
# 落とし、引用符は記号だけ外したうえで、行・セグメントごとに
# 「git のサブコマンドは何か」をトークンとして解釈する。
#
# 引用符の中身ごと落とすと、逆に `sh -c "git push --force origin main"` のように
# 引用符へ入れるだけで検査から消せてしまう。落とすのは説明文だけにする。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\Common.ps1')

# リモートにブランチが 1 つも無いか（＝作られた直後の空のリポジトリか）。
#
# 空のリモートに main を初めて置くときは、守るべき履歴がまだ無い。
# ここを塞ぐとリポジトリの初期セットアップができなくなる。
#
# 「そのブランチがまだ無いなら通す」という緩い条件にはしない。それだと
# origin に master が無いリポジトリで master を作れてしまい、保護が骨抜きになる。
function Test-RemoteIsEmpty {
    param(
        [Parameter(Mandatory)][string]$GitExe,
        [Parameter(Mandatory)][string]$RepoRoot
    )

    $result = Invoke-Git -GitExe $GitExe -RepoRoot $RepoRoot -GitArgs @('ls-remote', '--heads', 'origin')

    # 問い合わせに失敗したときは「空ではない」とみなして安全側に倒す。
    if (-not $result.Ok) { return $false }
    return [string]::IsNullOrWhiteSpace($result.StdOut)
}

# push 先として指定された参照が main / master を指しているか。
#
# refspec（src:dst）なら送り先は dst 側。`refs/heads/` の接頭辞だけを外して比較する。
# `feature/main` のような作業ブランチを保護ブランチと取り違えないよう、
# パス区切りで分割して末尾だけを見る、といったことはしない。
function Test-ProtectedRef {
    param([string]$Value)

    $destination = ($Value -split ':')[-1]
    $destination = $destination -replace '^refs/heads/', ''
    return @('main', 'master') -contains $destination
}

$hookInput = Read-HookInput
$command = [string](Get-Prop $hookInput 'tool_input.command' '')
if ([string]::IsNullOrWhiteSpace($command)) { exit 0 }
# 解析（説明文の除去、パス付き実行ファイルの正規化、サブコマンドの切り出し）は
# lib\Common.ps1 と guard-gh.ps1 で共有する。
#
# 早期脱出の判定も正規化した文字列に対して行う。生の文字列で見ると
# `& "C:\Program Files\Git\cmd\git.exe" push --force origin main` のような
# フルパス呼び出しを取りこぼす。
$inspectable = ConvertTo-BareCommand -Text (ConvertTo-InspectableCommand $command) -Name 'git'
if ($inspectable -notmatch '(^|[\s;&|(])git(\.exe)?\s') { exit 0 }

# 値を1つ取るグローバルオプション。読み飛ばしを漏らすと、その値をサブコマンドと
# 取り違えて検査全体がすり抜ける（git --git-dir X push --force origin main）。
$invocations = @(Get-CommandInvocation -CommandText $inspectable -Name 'git' `
        -ValueOptions @('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path'))
if ($invocations.Count -eq 0) { exit 0 }

$gitExe = Get-GitExe
if (-not $gitExe) { exit 0 }

$cwd = Get-Prop $hookInput 'cwd' (Get-Location).Path
$repoRoot = Get-RepoRoot -GitExe $gitExe -Directory $cwd
if (-not $repoRoot) { exit 0 }

$branch = Get-CurrentBranch -GitExe $gitExe -RepoRoot $repoRoot

foreach ($invocation in $invocations) {
    $arguments = $invocation.Arguments
    $positional = @($arguments | Where-Object { -not $_.StartsWith('-') })

    switch ($invocation.Subcommand) {
        'push' {
            # 短縮フラグは結合できる（git push -fu origin x）。-contains では拾えない。
            $forced = $arguments | Where-Object {
                $_ -eq '--force' -or $_ -match '^-[a-zA-Z]*f[a-zA-Z]*$'
            }
            if ($forced -and -not ($arguments -contains '--force-with-lease')) {
                Deny-Tool @"
強制 push は禁止されています（他の人やレビュー中の PR のコミットを消してしまうため）。

どうしても履歴を書き換える必要がある場合は、消えるコミットが無いことを保証する
    git push --force-with-lease
を使い、その理由を PR に書いてください。

止めたコマンド:
$($invocation.Text)
"@
            }

            if ($arguments -contains '--delete') {
                foreach ($value in $positional) {
                    if (Test-ProtectedRef $value) {
                        Deny-Tool "リモートの main / master ブランチは削除できません。"
                    }
                }
            }

            # 明示的に main / master を push しようとしている（refspec も含む）。
            foreach ($value in ($positional | Select-Object -Skip 1)) {
                if (-not (Test-ProtectedRef $value)) { continue }

                # 作られた直後の空のリモートへの初回 push。守る履歴が無いので通す。
                if (Test-RemoteIsEmpty -GitExe $gitExe -RepoRoot $repoRoot) { continue }

                Deny-Tool @"
main / master への直接 push は禁止されています（CLAUDE.md の規約）。

変更はブランチに push して PR を作ってください:
    git push -u origin <ブランチ名>
    gh pr create

止めたコマンド:
$($invocation.Text)
"@
            }

            # 送り先の指定が無い push は、現在のブランチをそのまま送る。
            if ($positional.Count -le 1 -and (Test-ProtectedBranch $branch) -and
                -not (Test-RemoteIsEmpty -GitExe $gitExe -RepoRoot $repoRoot)) {
                Deny-Tool @"
現在 $branch ブランチにいるため push できません（送り先を省いた push は $branch を push します）。

作業用ブランチに切り替えてから push してください:
    git switch -c feature/<機能名>
    git push -u origin feature/<機能名>
"@
            }
        }

        'commit' {
            if (Test-ProtectedBranch $branch) {
                Deny-Tool @"
$branch ブランチへの直接コミットは禁止されています（CLAUDE.md の規約）。

先にブランチを切ってください。ステージ済み・未ステージの変更はそのまま引き継がれます:
    git switch -c feature/<機能名>
    git commit -m "..."
"@
            }
        }

        'branch' {
            if ($arguments -contains '-D' -or $arguments -contains '-d') {
                foreach ($value in $positional) {
                    if (Test-ProtectedRef $value) {
                        Deny-Tool "main / master ブランチは削除できません。"
                    }
                }
            }
        }
    }
}

exit 0
