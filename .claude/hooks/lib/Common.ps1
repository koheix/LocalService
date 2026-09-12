# llm-console フック共通ユーティリティ。
#
# 各フックスクリプトから dot-source して使う。
# フックは Claude Code のプロセスから起動されるため、Claude Code 起動後に
# インストールされたツール（git / gh）が PATH に載っていないことがある。
# そのため実行ファイルは PATH だけに頼らず既知の場所も探す。

Set-StrictMode -Version Latest

# git / gh の標準出力は [Console]::OutputEncoding で解釈される。Windows PowerShell 5.1 の
# 既定は OEM コードページ（日本語環境では CP932）なので、UTF-8 で出てくる日本語の
# ブランチ名や Issue のタイトルが化ける（セッション開始の通知で実際に化けた）。
# 読み取り側を UTF-8 に固定する。コンソールを持たない環境では設定できないことがあるので、
# 失敗しても止めない（フックは落ちてはいけない）。
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {
}

# stdin の JSON を読む。空・壊れている場合は空オブジェクトを返す（フックは落ちてはいけない）。
#
# [Console]::In をそのまま使うと Windows PowerShell 5.1 は OEM コードページ（日本語環境では
# CP932）で解釈するため、UTF-8 で渡ってくる日本語のパスやコマンドが化ける。生バイトを
# UTF-8 として自分でデコードする。
function Read-HookInput {
    try {
        $stream = [Console]::OpenStandardInput()
        $reader = New-Object System.IO.StreamReader($stream, (New-Object System.Text.UTF8Encoding($false)))
        try { $raw = $reader.ReadToEnd() } finally { $reader.Dispose() }
    } catch {
        return [pscustomobject]@{}
    }

    if ([string]::IsNullOrWhiteSpace($raw)) { return [pscustomobject]@{} }
    try {
        return (ConvertFrom-Json -InputObject $raw)
    } catch {
        return [pscustomobject]@{}
    }
}

# PSCustomObject からドット区切りのパスで値を取り出す。存在しなければ $Default。
# StrictMode 下で「無いプロパティ参照」で落ちないようにするためのヘルパー。
function Get-Prop {
    param($Object, [string]$Path, $Default = $null)

    $current = $Object
    foreach ($segment in $Path.Split('.')) {
        if ($null -eq $current) { return $Default }
        if ($current -isnot [psobject]) { return $Default }
        $property = $current.PSObject.Properties[$segment]
        if ($null -eq $property) { return $Default }
        $current = $property.Value
    }
    if ($null -eq $current) { return $Default }
    return $current
}

# 実行ファイルを PATH → 既知のインストール先の順に探す。見つからなければ $null。
function Find-Executable {
    param([string]$Name, [string[]]$Fallbacks = @())

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return @($command)[0].Source }

    foreach ($candidate in $Fallbacks) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-GitExe {
    return Find-Executable -Name 'git' -Fallbacks @(
        (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Git\cmd\git.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
    )
}

function Get-GhExe {
    return Find-Executable -Name 'gh' -Fallbacks @(
        (Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'GitHub CLI\gh.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\GitHub CLI\gh.exe')
    )
}

# git を実行し、終了コード・標準出力・標準エラーを分けて返す。
# ネイティブコマンドの stderr を 2>&1 でパイプに流すと Windows PowerShell 5.1 では
# NativeCommandError になり $? が壊れるため、stderr は一時ファイルに逃がす。
function Invoke-Git {
    param(
        [Parameter(Mandatory)][string]$GitExe,
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string[]]$GitArgs
    )

    $errorFile = [System.IO.Path]::GetTempFileName()
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $stdout = & $GitExe -C $RepoRoot @GitArgs 2>$errorFile
        $exitCode = $LASTEXITCODE

        $stderr = ''
        if (Test-Path -LiteralPath $errorFile) {
            $stderr = Get-Content -LiteralPath $errorFile -Raw -ErrorAction SilentlyContinue
        }
        if ($null -eq $stderr) { $stderr = '' }

        return [pscustomobject]@{
            ExitCode = $exitCode
            StdOut   = (($stdout | Out-String).Trim())
            StdErr   = $stderr.Trim()
            Ok       = ($exitCode -eq 0)
        }
    } finally {
        $ErrorActionPreference = $previous
        Remove-Item -LiteralPath $errorFile -Force -ErrorAction SilentlyContinue
    }
}

# 指定ディレクトリを含む git リポジトリのルートを返す。リポジトリ外なら $null。
function Get-RepoRoot {
    param([Parameter(Mandatory)][string]$GitExe, [Parameter(Mandatory)][string]$Directory)

    if (-not (Test-Path -LiteralPath $Directory)) { return $null }
    $result = Invoke-Git -GitExe $GitExe -RepoRoot $Directory -GitArgs @('rev-parse', '--show-toplevel')
    if (-not $result.Ok -or [string]::IsNullOrWhiteSpace($result.StdOut)) { return $null }
    return ($result.StdOut -replace '/', '\')
}

function Get-CurrentBranch {
    param([Parameter(Mandatory)][string]$GitExe, [Parameter(Mandatory)][string]$RepoRoot)

    $result = Invoke-Git -GitExe $GitExe -RepoRoot $RepoRoot -GitArgs @('rev-parse', '--abbrev-ref', 'HEAD')
    if (-not $result.Ok) { return $null }
    return $result.StdOut
}

# main / master などの保護対象ブランチか。
function Test-ProtectedBranch {
    param([string]$Branch)
    return @('main', 'master') -contains $Branch
}

# フック用 JSON を stdout に書く。改行は付けない（Claude Code が JSON として解釈するため）。
#
# こちらも [Console]::Out 経由だと OEM コードページで出力されて日本語が壊れるので、
# UTF-8 の生バイトを標準出力に直接書く。
function Write-HookJson {
    param([Parameter(Mandatory)]$Payload)

    $json = ConvertTo-Json -InputObject $Payload -Depth 10 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $stream = [Console]::OpenStandardOutput()
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
}

# コマンド文字列を「検査できる形」に整える。
#
# 落とすのは説明文だけにする。以前は引用符で囲まれた部分を中身ごと落としていたが、
# それだと危険なコマンドを引用符に入れるだけで検査から消せてしまった。
#
#   sh -c "gh pr merge 12"        引用符ごと消えて素通り
#   gh pr "merge" 12              サブコマンドだけ消えて素通り
#   git commit -m "don't"…"won't" アポストロフィが対になり、間の行ごと消える
#
# そこで3段に分ける。
#
#   1. メッセージ引数（-m / --body など）の中身だけを落とす。誤検知の実例はすべてこの形
#   2. ヒアドキュメントの本文を落とす。ただし受け手がシェルなら落とさない
#   3. 残った引用符は「記号だけ」外す。トークンの区切りは保つ
function ConvertTo-InspectableCommand {
    param([string]$Text)

    $result = Remove-MessageArguments $Text
    $result = Remove-HeredocBodies $result
    # 引用符は空白に置き換える。消すとトークンがくっついて別語になる。
    return ($result -replace '[''"]', ' ')
}

# 引用符の中にある区切り文字だけを隠す。
#
# コマンドを行・文・パイプ段に割るとき、引用符の中の ; | & > まで区切りとして
# 扱うと、引数の中身で構造を取り違える。
#   grep -E '"commit"|"failed"' <ファイル>   ← 引数の | を段の区切りと誤読した（実際に踏んだ）
# 中身そのものは残すので、パスなどの照合には影響しない。
# 割り終わったら Restore-Separators で元に戻す。
# 区切り文字と、それを隠すための対応表。制御文字はコマンドに現れない。
$script:SeparatorMasks = @(
    @{ Char = ';'; Mask = [string][char]1 },
    @{ Char = '&'; Mask = [string][char]2 },
    @{ Char = '|'; Mask = [string][char]3 },
    @{ Char = '>'; Mask = [string][char]4 }
)

function Protect-QuotedSeparators {
    param([string]$Text)

    $singleLine = [System.Text.RegularExpressions.RegexOptions]::Singleline
    $mask = {
        param($match)
        $value = $match.Value
        foreach ($entry in $script:SeparatorMasks) {
            $value = $value.Replace($entry.Char, $entry.Mask)
        }
        return $value
    }
    $result = [regex]::Replace($Text, "'[^']*'", $mask, $singleLine)
    return [regex]::Replace($result, '"[^"]*"', $mask, $singleLine)
}

function Restore-Separators {
    param([string]$Text)

    $result = $Text
    foreach ($entry in $script:SeparatorMasks) {
        $result = $result.Replace($entry.Mask, $entry.Char)
    }
    return $result
}

# -m / --message / -b / --body / -t / --title などの引数の中身を落とす。
# コミットメッセージや PR 本文に規約そのものを書くと誤検知する、という事故への対処。
function Remove-MessageArguments {
    param([string]$Text)

    $flags = 'm|message|b|body|t|title|d|description|F|body-file'
    # 値は PowerShell / bash のヒアドキュメント、または通常の引用符。
    $value = "@'[\s\S]*?'@|@""[\s\S]*?""@|'[^']*'|""[^""]*"""
    $pattern = "(--?(?:$flags)(?:=|\s+))(?:$value)"

    return [regex]::Replace($Text, $pattern, '$1''''')
}

# bash のヒアドキュメント本文を落とす。
#
# ただし受け手がシェルインタプリタなら落とさない。
#
#   bash <<'EOF'
#   gh pr merge 12
#   EOF
#
# は「説明文」ではなく実行される。落とすと検査をすり抜ける。
function Remove-HeredocBodies {
    param([string]$Text)

    $singleLine = [System.Text.RegularExpressions.RegexOptions]::Singleline

    $heredoc = '(?<lead>[^\r\n]*?)<<-?\s*(?<q>[''"]?)(?<tag>[A-Za-z_][A-Za-z0-9_]*)\k<q>(?<rest>[^\r\n]*)\r?\n[\s\S]*?\r?\n[ \t]*\k<tag>'
    # 標準入力を受け取って実行するもの。シェルだけではない。
    # node <<'JS' … JS のように、非シェルのインタプリタでも本文は実行される。
    $interpreter = '(^|[\s;&|(])(bash|sh|zsh|ksh|dash|pwsh|powershell(\.exe)?|cmd(\.exe)?|eval|xargs|iex|Invoke-Expression|node|deno|python3?|perl|ruby|php)(\s|$)'

    # 受け手は前にも後ろにも書ける。
    #   bash <<'EOF' ... EOF
    #   cat <<'EOF' | bash ... EOF
    # どちらも本文が実行されるので落としてはいけない。lead だけを見ると後者を取り逃す。
    #
    # 落とすのは本文だけ。開始行の残り（rest）は残す。ここにはリダイレクト先や
    # 続きのコマンドが書かれる。
    #   cat <<'EOF' > reports/verification.json   ← 送り先が消えると検出できない
    #   cat <<'EOF' > /dev/null; gh pr merge 12   ← 続きのコマンドが消える
    $evaluator = {
        param($match)
        $lead = $match.Groups['lead'].Value
        $rest = $match.Groups['rest'].Value
        if ($lead -match $interpreter -or $rest -match $interpreter) { return $match.Value }
        return "$lead '' $rest"
    }
    $result = [regex]::Replace($Text, $heredoc, $evaluator)

    # PowerShell のヒアドキュメント。値としてしか使えないので中身を落とす。
    # ただし Invoke-Expression に渡していれば実行されるので、そのときは残す。
    if ($result -notmatch '(^|[\s;&|(])(iex|Invoke-Expression)(\s|$)') {
        $result = [regex]::Replace($result, "@'.*?'@", "''", $singleLine)
        $result = [regex]::Replace($result, '@".*?"@', '""', $singleLine)
    }
    return $result
}

# 実行ファイルのパス指定を、素のコマンド名に均す。
#
#   & "C:\Program Files\GitHub CLI\gh.exe" pr merge 12
#   C:\tools\gh.exe pr merge 12
#
# のように呼ばれると、名前の直前がパス区切りや引用符になり「gh というコマンド」を
# 見つけられず、ガードを1行で迂回できてしまう。
#
# 実際の呼び出しは ConvertTo-InspectableCommand の後なので引用符は既に外れている。
# 引用符付きの分岐は、生の文字列を渡された場合に備えた保険として残してある。
function ConvertTo-BareCommand {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Name
    )

    $escaped = [regex]::Escape($Name)

    # 引用符で囲まれた、パス付き・パス無しの実行ファイル。
    $quoted = '["'']([^"'']*[\\/])?' + $escaped + '(\.exe)?["'']'
    $result = [regex]::Replace($Text, $quoted, " $Name ")

    # 引用符なしのパス付き実行ファイル。
    $bare = '(^|[\s;&|()])[^\s;&|()"'']*[\\/]' + $escaped + '(\.exe)?(?=[\s;&|()]|$)'
    $result = [regex]::Replace($result, $bare, " $Name ")

    return $result
}

# コマンド文字列から特定のコマンド（git / gh）の呼び出しを取り出し、
# サブコマンドと残りの引数に分解する。
#
# 単純な文字列検索にしないのは Remove-QuotedText と同じ理由。行とセグメントに割ってから
# トークンとして読む。ValueOptions には「値を1つ取るグローバルオプション」を渡す
# （git の -C <path>、gh の --repo <owner/name> など）。読み飛ばしを間違えると、
# その値をサブコマンドと取り違える。
function Get-CommandInvocation {
    param(
        [Parameter(Mandatory)][string]$CommandText,
        [Parameter(Mandatory)][string]$Name,
        [string[]]$ValueOptions = @()
    )

    $invocations = New-Object System.Collections.Generic.List[psobject]
    # 名前の直前は行頭・空白・区切り文字・パス区切りのいずれでもよい。
    # ConvertTo-BareCommand で均してあるが、取りこぼしに備えてここでも許す。
    $pattern = "(?:^|[\s;&|()])(?:[^\s;&|()]*[\\/])?$([regex]::Escape($Name))(?:\.exe)?\s+(.+)$"

    foreach ($line in ($CommandText -split '\r?\n')) {
        foreach ($segment in ($line -split '[;|&]')) {
            $trimmed = $segment.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }

            $match = [regex]::Match($trimmed, $pattern)
            if (-not $match.Success) { continue }

            $tokens = @($match.Groups[1].Value -split '\s+' | Where-Object { $_ -ne '' })

            $index = 0
            while ($index -lt $tokens.Count -and $tokens[$index].StartsWith('-')) {
                if ($ValueOptions -contains $tokens[$index]) { $index += 2 } else { $index += 1 }
            }
            if ($index -ge $tokens.Count) { continue }

            $arguments = @()
            if ($index + 1 -lt $tokens.Count) {
                $arguments = @($tokens[($index + 1)..($tokens.Count - 1)])
            }

            # サブコマンドの次の位置引数（gh なら pr merge の merge、git なら push の相手）。
            #
            # 単純に $Arguments[0] を見てはいけない。gh も git もサブコマンドの間に
            # フラグを挟める（gh pr --repo owner/name merge 12 は実際に動く）。
            # ここで読み飛ばさないと action が --repo になり、判定が丸ごと外れる。
            $actionIndex = 0
            while ($actionIndex -lt $arguments.Count -and $arguments[$actionIndex].StartsWith('-')) {
                if ($ValueOptions -contains $arguments[$actionIndex]) { $actionIndex += 2 } else { $actionIndex += 1 }
            }
            $action = if ($actionIndex -lt $arguments.Count) { $arguments[$actionIndex] } else { '' }

            $invocations.Add([pscustomobject]@{
                Subcommand = $tokens[$index]
                Action     = $action
                Arguments  = $arguments
                Text       = $trimmed
            })
        }
    }

    # そのまま返してパイプラインに展開させ、呼び出し側の @() で配列に受け直す。
    # ここで , を付けると二重に包まれてしまう。
    return $invocations.ToArray()
}

# PreToolUse でツール実行を拒否する。
function Deny-Tool {
    param([Parameter(Mandatory)][string]$Reason)

    Write-HookJson @{
        hookSpecificOutput = @{
            hookEventName            = 'PreToolUse'
            permissionDecision       = 'deny'
            permissionDecisionReason = $Reason
        }
    }
    exit 0
}

# 実行は止めずに、ユーザーと Claude の両方に注意を伝える。
function Write-Notice {
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$HookEventName = 'PreToolUse'
    )

    Write-HookJson @{
        systemMessage      = $Message
        hookSpecificOutput = @{
            hookEventName     = $HookEventName
            additionalContext = $Message
        }
    }
}

# 同期状態などのセッションローカルな状態ファイルの置き場所。
#
# ここに書いたファイルが git の作業ツリーを汚すと、「未コミットの変更あり」と判定した
# フック自身が次の編集を拒否してしまう。リポジトリ側の .gitignore に頼らず、
# ディレクトリ自身に「中身をすべて無視する」.gitignore を置いて自己完結させる。
function Get-StateFilePath {
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$Name)

    $directory = Join-Path $RepoRoot '.claude\state'
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $ignoreFile = Join-Path $directory '.gitignore'
    if (-not (Test-Path -LiteralPath $ignoreFile)) {
        # '*' は .gitignore 自身も対象にするため、このディレクトリは git から完全に見えなくなる。
        [System.IO.File]::WriteAllText($ignoreFile, "*`n", (New-Object System.Text.UTF8Encoding($false)))
    }

    return (Join-Path $directory $Name)
}

function Read-StateFile {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Write-StateFile {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)

    try {
        ConvertTo-Json -InputObject $Value -Depth 10 |
            Out-File -LiteralPath $Path -Encoding utf8 -Force
    } catch {
        # 状態の保存に失敗してもフック本体の判断には影響させない。
    }
}
