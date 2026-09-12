# PreToolUse (Edit|Write|NotebookEdit と Bash|PowerShell) フック。
#
# 検証の記録を、検証を課される側が自分で書けないようにする。
#
#   reports/verification.json   make test-report だけが書く
#   reports/review-record.json  scripts\record_review.py だけが書く
#
# これが無いと、guard-gh.ps1 の PR 作成ゲートは「合格証を自分で書ける」試験になる。
# 工程を飛ばしたことに気づけるようにする、というこの仕組みの目的そのものが崩れる。
#
# 編集ツールだけを塞いでも、シェルからのリダイレクトで書けてしまうので両方を見る。
#
# 「書き込みの手段」を並べて塞ぐ方式にはしない。列挙は必ず漏れる
# （node -e、[IO.File]::WriteAllText、Rename-Item… いくらでもある）。
# 逆にして、読み取りだと分かる形だけを通し、それ以外は拒否する。
#
# 正規の生成経路（make test-report / record_review.py）はコマンド行に
# 保護ファイル名が出てこないので、そもそも判定に掛からない。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\Common.ps1')

# 守る対象。リポジトリからの相対パス。
$ProtectedFiles = @(
    @{ Path = 'reports/verification.json'; Producer = 'make test-report' },
    @{ Path = 'reports/review-record.json'; Producer = 'python scripts/record_review.py --reviewers <名前> --verdict pass' }
)

# 保護ファイルに触れてよい読み取りコマンド。これ以外は書き込みとみなす。
$ReadOnlyCommands = @(
    'cat', 'type', 'head', 'tail', 'more', 'less', 'jq', 'grep', 'rg', 'diff',
    'Get-Content', 'gc', 'Select-String', 'sls', 'Test-Path', 'ls', 'dir',
    'Get-Item', 'Get-ChildItem'
)

function Deny-RecordWrite {
    param([Parameter(Mandatory)]$Protected)

    Deny-Tool @"
$($Protected.Path) を直接書き換えないでください。

このファイルは検証の記録です。自分で書けてしまうと、検証したことの記録に
意味が無くなります。次のコマンドで作り直してください。

  $($Protected.Producer)

なお、読み取りであっても cat / Get-Content / grep / jq などの既知の形以外は
書き込みとみなして拒否します（python -c での読み取りなど）。
中身を見たいだけなら cat か Get-Content を使ってください。
"@
}

# リダイレクトの送り先がそのファイルか。
#
# 「> の直後にそのパスがそのまま続く」形だけを見ると、`> ./reports/...` や
# 絶対パスで外れる。送り先のトークンを取り出して、末尾で照合する。
function Test-RedirectsTo {
    param([Parameter(Mandatory)][string]$Statement, [Parameter(Mandatory)][string]$Path)

    foreach ($match in [regex]::Matches($Statement, '>>?\s*\|?\s*(?<target>[^\s;&|]+)')) {
        $target = ($match.Groups['target'].Value -replace '\\', '/').TrimEnd('/')
        if ($target -eq $Path -or $target.EndsWith('/' + $Path)) { return $true }
    }
    return $false
}

$hookInput = Read-HookInput

# --- 編集ツール（Edit / Write / NotebookEdit）---
#
# NotebookEdit は file_path ではなく notebook_path を使うので両方を見る。
$filePath = [string](Get-Prop $hookInput 'tool_input.file_path' '')
if ([string]::IsNullOrWhiteSpace($filePath)) {
    $filePath = [string](Get-Prop $hookInput 'tool_input.notebook_path' '')
}

if (-not [string]::IsNullOrWhiteSpace($filePath)) {
    # パスの表記ゆれ（\ と /、大文字小文字）を吸収して末尾一致で見る。
    $normalizedPath = ($filePath -replace '\\', '/').ToLowerInvariant()
    foreach ($protected in $ProtectedFiles) {
        $target = $protected.Path.ToLowerInvariant()
        if ($normalizedPath.EndsWith('/' + $target) -or $normalizedPath -eq $target) {
            Deny-RecordWrite $protected
        }
    }

    # ガード自身の編集は止めない（誤検知を直す必要が現実にある）。
    # ただし黙って通すと、ガードを無効化する変更が誰にも見えないまま通る。
    # 記録を作る側のスクリプトも、書き換えればゲートを無力化できる。
    if ($normalizedPath -match '/(\.claude/(hooks/|settings\.json)|scripts/(record_review\.py|gen_report\.py))') {
        Write-Notice -Message @"
ガードの設定そのものを変更しています: $filePath

無効化や緩和が意図した変更かどうか、PR で必ず説明してください。
"@
        exit 0
    }
}

# --- シェル（Bash / PowerShell）---
$command = [string](Get-Prop $hookInput 'tool_input.command' '')
if ([string]::IsNullOrWhiteSpace($command)) { exit 0 }

# 先に引用符の中の区切り文字を隠し、そのあとで説明文（メッセージ引数・
# ヒアドキュメント本文）を落とす。順序が逆だと、引用符が外れたあとでは
# どれが引数の中の | だったのか分からなくなる。
$prepared = ConvertTo-InspectableCommand (Protect-QuotedSeparators $command)

foreach ($line in ($prepared -split '\r?\n')) {
    # まず文の切れ目で割り、次にパイプの段で割る。
    #
    # 文で割らないと `make test-report; Set-Content <記録>` を通してしまい、
    # 段で割らないと `Get-Content x | Set-Content <記録>` を通してしまう。
    # どちらも実際に空いていた穴。
    foreach ($statement in ($line -split '[;&]')) {
        foreach ($stage in ($statement -split '\|')) {
            $trimmed = (Restore-Separators $stage).Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }

            foreach ($protected in $ProtectedFiles) {
                $normalizedStage = $trimmed -replace '\\', '/'

                # 相対パスの一致だけを見ると `cd reports; Set-Content verification.json`
                # で外れる。ファイル名だけでも拾う。名前が特徴的なので誤爆は考えにくい。
                $baseName = ($protected.Path -split '/')[-1]
                $mentioned =
                    ($normalizedStage -match [regex]::Escape($protected.Path)) -or
                    ($normalizedStage -match ('(^|[\s''"/=])' + [regex]::Escape($baseName)))
                if (-not $mentioned) { continue }

                # 送り先が保護ファイルなら、読み取りコマンドであっても書き込み。
                if (Test-RedirectsTo -Statement $normalizedStage -Path $baseName) {
                    Deny-RecordWrite $protected
                }

                # 先頭のトークンが読み取りコマンドでなければ拒否する。
                $first = ($normalizedStage -split '\s+')[0]
                $first = ($first -split '/')[-1]
                if ($ReadOnlyCommands -notcontains $first) {
                    Deny-RecordWrite $protected
                }
            }
        }
    }
}

exit 0
