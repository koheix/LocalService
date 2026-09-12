# PostToolUse (Edit|Write) フック。
#
# 編集直後にファイルの健全性を確認し、壊れていればその場で Claude に差し戻す。
# テスト実行まで気づかない構文エラー・lint 違反を、次の 1 手で潰せるようにするのが狙い。
#
#   .py  … ruff format --check（gateway/ 配下のみ。設定は gateway/pyproject.toml にある）と
#          ruff check で差し戻す（mypy は CLAUDE.md の方針により未導入なので行わない）
#   .ps1 … PowerShell パーサで構文チェック

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\Common.ps1')

# ruff の出力は UTF-8 だが、Windows PowerShell 5.1 は既定で OEM コードページとして読むため、
# 日本語を含む指摘が文字化けする。子プロセス出力の解釈を UTF-8 に揃える。
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

function Write-Block {
    param([Parameter(Mandatory)][string]$Reason)

    Write-HookJson @{
        decision = 'block'
        reason   = $Reason
    }
    exit 0
}

# ruff は PATH にあればそれを使う。venv を activate せずに Claude Code から呼ばれることも
# あるので、無ければ `python -m ruff` へフォールバックする。
function Get-RuffInvoker {
    $ruffExe = Find-Executable -Name 'ruff'
    if ($ruffExe) { return @($ruffExe) }

    foreach ($name in @('py', 'python', 'python3')) {
        $exe = Find-Executable -Name $name
        if ($exe) { return @($exe, '-m', 'ruff') }
    }
    return $null
}

# ruff を実行し、終了コードと標準出力・標準エラーをまとめて返す。
function Invoke-Ruff {
    param(
        [Parameter(Mandatory)][string[]]$Invoker,
        [Parameter(Mandatory)][string[]]$RuffArgs
    )

    $exe = $Invoker[0]
    $prefixArgs = @()
    if ($Invoker.Count -gt 1) { $prefixArgs = $Invoker[1..($Invoker.Count - 1)] }
    $allArgs = @($prefixArgs + $RuffArgs)

    $errorFile = [System.IO.Path]::GetTempFileName()
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $stdout = & $exe @allArgs 2>$errorFile | Out-String
        $exitCode = $LASTEXITCODE
        $stderr = Get-Content -LiteralPath $errorFile -Raw -ErrorAction SilentlyContinue
        if ($null -eq $stderr) { $stderr = '' }
        return [pscustomobject]@{ ExitCode = $exitCode; Output = ($stdout + $stderr).Trim() }
    } finally {
        $ErrorActionPreference = $previous
        Remove-Item -LiteralPath $errorFile -Force -ErrorAction SilentlyContinue
    }
}

$hookInput = Read-HookInput
$filePath = Get-Prop $hookInput 'tool_response.filePath'
if (-not $filePath) { $filePath = Get-Prop $hookInput 'tool_input.file_path' }
if (-not $filePath -or -not (Test-Path -LiteralPath $filePath -PathType Leaf)) { exit 0 }

$extension = [System.IO.Path]::GetExtension($filePath).ToLowerInvariant()

# --- PowerShell スクリプト（.claude/hooks 自身を含む）---
if ($extension -eq '.ps1') {
    # Windows PowerShell 5.1 は BOM の無い .ps1 をシステムの ANSI コードページとして読む。
    # 日本語を含むスクリプトが BOM 無しで保存されると文字化けし、ヒアドキュメントの
    # 終端すら壊れてスクリプト全体が構文エラーになる。編集ツールは BOM を落とすので、
    # ここで気づいたら黙って付け直す。
    $bytes = [System.IO.File]::ReadAllBytes($filePath)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $hasNonAscii = $false
    foreach ($byte in $bytes) { if ($byte -gt 0x7F) { $hasNonAscii = $true; break } }

    if ($hasNonAscii -and -not $hasBom) {
        try {
            $text = [System.Text.UTF8Encoding]::new($false).GetString($bytes)
            [System.IO.File]::WriteAllText($filePath, $text, [System.Text.UTF8Encoding]::new($true))
            Write-Notice -HookEventName 'PostToolUse' `
                -Message "$filePath に UTF-8 BOM を付け直しました（BOM が無いと Windows PowerShell 5.1 が日本語を文字化けさせ、スクリプトが動かなくなるため）。"
        } catch {
            Write-Block "$filePath は日本語を含みますが UTF-8 BOM がありません。Windows PowerShell 5.1 が文字化けさせるため、BOM 付き UTF-8 で保存し直してください。"
        }
    }

    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($filePath, [ref]$tokens, [ref]$errors) | Out-Null

    if ($errors -and $errors.Count -gt 0) {
        $details = ($errors | ForEach-Object { "  {0} 行目: {1}" -f $_.Extent.StartLineNumber, $_.Message }) -join "`n"
        Write-Block "$filePath に PowerShell の構文エラーがあります。修正してください。`n$details"
    }
    exit 0
}

if ($extension -ne '.py') { exit 0 }

# --- Python（gateway/ 配下のみ。ruff の設定は gateway/pyproject.toml にある）---
$gitExe = Get-GitExe
$repoRoot = $null
if ($gitExe) { $repoRoot = Get-RepoRoot -GitExe $gitExe -Directory (Split-Path -Path $filePath -Parent) }
if (-not $repoRoot) { exit 0 }

$gatewayRoot = Join-Path $repoRoot 'gateway'
if (-not (Test-Path -LiteralPath (Join-Path $gatewayRoot 'pyproject.toml'))) { exit 0 }

# gateway/ の外（repo ルートの scripts/ 等）は対象にしない。ruff の設定・依存が無いため。
$normalizedFile = ($filePath -replace '\\', '/').ToLowerInvariant()
$normalizedGatewayRoot = (($gatewayRoot -replace '\\', '/').TrimEnd('/') + '/').ToLowerInvariant()
if (-not $normalizedFile.StartsWith($normalizedGatewayRoot)) { exit 0 }

$invoker = Get-RuffInvoker
# ruff も python も見つからない環境では、入っていないことを「指摘ゼロ」ではなく
# 「差し戻し」と誤認しないよう、チェックを飛ばす。
if (-not $invoker) { exit 0 }

$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    Push-Location -LiteralPath $gatewayRoot
    try {
        $relativeFile = (Resolve-Path -LiteralPath $filePath -Relative) -replace '^\.[\\/]', ''

        # 1. フォーマット差分。直しておくべきものなので差し戻す。
        $format = Invoke-Ruff -Invoker $invoker -RuffArgs @('format', '--check', '--diff', $relativeFile)
        if ($format.ExitCode -ne 0) {
            Write-Block "$filePath は ruff format の対象です。`n$($format.Output)`n`n'ruff format $relativeFile'（gateway/ 直下で実行、または make fmt）で直せます。"
        }

        # 2. lint の指摘。
        $lint = Invoke-Ruff -Invoker $invoker -RuffArgs @('check', $relativeFile)
        if ($lint.ExitCode -ne 0) {
            Write-Block "$filePath に ruff の指摘があります。`n$($lint.Output)`n`n自動修正できるものは 'ruff check --fix $relativeFile'（gateway/ 直下、または make fmt）で直せます。"
        }
    } finally {
        Pop-Location
    }
} finally {
    $ErrorActionPreference = $previous
}

exit 0
