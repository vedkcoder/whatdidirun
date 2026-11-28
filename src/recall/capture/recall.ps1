# recall: capture every interactive PowerShell command (with context) into the JSONL spool.
# Works in Windows PowerShell 5.1 and PowerShell 7+. Add to your profile ($PROFILE):
#     . "<path>\recall.ps1"
# or let `recall init powershell --install` (the Windows installer does this) add it for you.
#
# It wraps your existing prompt function, so load it AFTER prompt themes (oh-my-posh, starship, ...).
# Commands typed with a leading space are not recorded.

if ($global:__recall_loaded) { return }
$global:__recall_loaded = $true

$global:__recall_windows = ($PSVersionTable.PSEdition -eq 'Desktop') -or $IsWindows
$global:__recall_data = if ($env:RECALL_DATA_DIR) { $env:RECALL_DATA_DIR }
    elseif ($global:__recall_windows) { Join-Path $env:LOCALAPPDATA 'recall' }
    else { Join-Path $HOME '.local/share/recall' }
$global:__recall_spool = Join-Path $global:__recall_data 'spool.jsonl'
if (-not (Test-Path -LiteralPath $global:__recall_data)) {
    New-Item -ItemType Directory -Force -Path $global:__recall_data | Out-Null
}
# Prefer the recall.exe this script ships with (Windows app: <app>\_internal\recall\capture\recall.ps1), so the
# hook works even when PATH hasn't caught up yet (e.g. Windows Terminal started before the install).
$global:__recall_bin = $null
$__recall_bundled = Join-Path $PSScriptRoot '..\..\..\recall.exe'
if ($global:__recall_windows -and (Test-Path -LiteralPath $__recall_bundled)) {
    $global:__recall_bin = (Resolve-Path -LiteralPath $__recall_bundled).ProviderPath
}
Remove-Variable __recall_bundled
if (-not $global:__recall_bin) {
    $global:__recall_bin = (Get-Command recall -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
}
$global:__recall_shell = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
$global:__recall_host = [Environment]::MachineName
$global:__recall_session = "$($global:__recall_host)-$PID-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
$global:__recall_utf8 = New-Object System.Text.UTF8Encoding $false
$global:__recall_inv = [Globalization.CultureInfo]::InvariantCulture
$global:__recall_count = 0
$global:__recall_last_id = (Get-History -Count 1).Id      # ignore anything run before we loaded
$global:__recall_last_lec = $global:LASTEXITCODE
$global:__recall_cwd = $null

function global:__recall_cwd_now {
    $loc = $ExecutionContext.SessionState.Path.CurrentLocation
    if ($loc.Provider.Name -eq 'FileSystem') { $loc.ProviderPath } else { $null }
}

function global:__recall_epoch([datetime]$t) {
    ([DateTimeOffset]$t).ToUnixTimeMilliseconds() / 1000.0
}

function global:__recall_ingest_bg {
    if (-not $global:__recall_bin -or $env:RECALL_AUTO_INGEST -eq '0') { return }
    try {
        $opts = @{ FilePath = $global:__recall_bin; ArgumentList = @('ingest', '--quiet', '--no-wait') }
        if ($global:__recall_windows) { $opts.WindowStyle = 'Hidden' }
        Start-Process @opts -ErrorAction SilentlyContinue | Out-Null
    } catch { }
}

function global:__recall_record([bool]$ok, $lec) {
    $h = Get-History -Count 1
    $changed = ($lec -ne $global:__recall_last_lec)
    $global:__recall_last_lec = $lec
    if (-not $h -or $h.Id -eq $global:__recall_last_id) { return }
    $global:__recall_last_id = $h.Id
    $cmd = $h.CommandLine
    if (-not $cmd -or $cmd.StartsWith(' ')) { return }

    # Native programs set $LASTEXITCODE; cmdlets only set $?.
    $exit = if ($ok) { 0 } elseif ($changed -and $null -ne $lec -and $lec -ne 0) { [int]$lec } else { 1 }
    $rec = [ordered]@{
        cmd     = $cmd
        cwd     = $global:__recall_cwd                      # where the command started, not where it ended
        exit    = $exit
        start   = (__recall_epoch $h.StartExecutionTime).ToString($global:__recall_inv)
        end     = (__recall_epoch $h.EndExecutionTime).ToString($global:__recall_inv)
        host    = $global:__recall_host
        session = $global:__recall_session
        shell   = $global:__recall_shell
    }
    $json = ConvertTo-Json -InputObject $rec -Compress
    [IO.File]::AppendAllText($global:__recall_spool, $json + "`n", $global:__recall_utf8)

    $every = if ($env:RECALL_INGEST_EVERY) { [int]$env:RECALL_INGEST_EVERY } else { 25 }
    $global:__recall_count++
    if ($every -gt 0 -and $global:__recall_count % $every -eq 0) { __recall_ingest_bg }
}

$global:__recall_orig_prompt = $function:prompt
function global:prompt {
    $ok = $?
    $lec = $global:LASTEXITCODE
    try { __recall_record $ok $lec } catch { }
    if (-not $global:__recall_key_bound) { try { __recall_bind_key } catch { } }
    $global:__recall_cwd = __recall_cwd_now
    $global:LASTEXITCODE = $lec
    if (-not $ok) { Write-Error 'recall' -ErrorAction Ignore }   # give the wrapped prompt the original $? back
    if ($global:__recall_orig_prompt) { & $global:__recall_orig_prompt } else { "PS $($executionContext.SessionState.Path.CurrentLocation)> " }
}
$global:__recall_cwd = __recall_cwd_now

# Ctrl-G: natural-language picker; the chosen command replaces the current line.
# The console host loads PSReadLine only AFTER the profile has run, so this is retried from the prompt
# until PSReadLine is there.
$global:__recall_key_bound = $false
function global:__recall_bind_key {
    if ($global:__recall_key_bound -or -not $global:__recall_bin -or -not (Get-Module PSReadLine)) { return }
    $global:__recall_key_bound = $true
    $chord = if ($env:RECALL_KEY) { $env:RECALL_KEY } else { 'Ctrl+g' }
    Set-PSReadLineKeyHandler -Chord $chord -BriefDescription 'recall' `
        -Description 'Search your command history in plain English (What did I Run ??)' -ScriptBlock {
        $line = $null; $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
        $prevEnc = [Console]::OutputEncoding
        $prevPy = $env:PYTHONIOENCODING
        try {
            [Console]::OutputEncoding = [Text.Encoding]::UTF8
            $env:PYTHONIOENCODING = 'utf-8'
            $out = (& $global:__recall_bin tui --query $line) -join "`n"
        } finally {
            [Console]::OutputEncoding = $prevEnc
            $env:PYTHONIOENCODING = $prevPy
        }
        [Microsoft.PowerShell.PSConsoleReadLine]::InvokePrompt()
        if ($out) {
            [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $out)
        }
    }
}
__recall_bind_key

# Drain the spool + embed new commands in the background on shell start.
__recall_ingest_bg
