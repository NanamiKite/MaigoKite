param(
    [string]$Exe,
    [string]$OutputRoot = (Join-Path $PSScriptRoot 'results'),
    [string]$Python,
    [ValidateSet('E01-free', 'E02-material', 'E03-resistor', 'E04-ideal', 'E01-free-refined', 'E05-soil',
        'G01-inverted-v', 'G02-square-loop', 'G03-yagi')]
    [string]$Case,
    [switch]$InspectOnly,
    [switch]$Sweep,
    [switch]$NextBatch,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# ASCII source also works in Windows PowerShell 5.1 without a UTF-8 BOM.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$captureExitCode = 1
$transcriptStarted = $false
$launcherLog = $null

try {
    if ($NextBatch -and ($Sweep -or $InspectOnly)) {
        throw '-NextBatch cannot be combined with -Sweep or -InspectOnly.'
    }
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    [IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
    $runStamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $launcherLog = Join-Path $OutputRoot ('launcher-' + $runStamp + '.log')
    Start-Transcript -LiteralPath $launcherLog -NoClobber | Out-Null
    $transcriptStarted = $true
    Write-Host ('[MMANA capture] Launcher log: ' + $launcherLog)
    if (-not $Exe) {
        Add-Type -AssemblyName System.Windows.Forms
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = 'Select your MMANA-GAL executable'
        $picker.Filter = 'MMANA-GAL (*.exe)|*.exe'
        try {
            if ($picker.ShowDialog() -ne 'OK') { throw 'Executable selection cancelled; nothing was started.' }
            $Exe = $picker.FileName
        }
        finally { $picker.Dispose() }
    }
    $Exe = (Resolve-Path -LiteralPath $Exe).Path
    if ([IO.Path]::GetExtension($Exe) -ne '.exe') { throw 'Select the MMANA-GAL .exe file.' }

    # Reuse an isolated environment, without installing the Agent or changing its dependencies.
    $envRoot = Join-Path $env:LOCALAPPDATA 'MaigoKite-MMANA-Capture\venv'
    $runner = Join-Path $envRoot 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $runner)) {
        $pythonArgs = @()
        if ($Python) { $bootstrap = $Python }
        elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $bootstrap = 'py'
            $pythonArgs = @('-3')
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { $bootstrap = 'python' }
        else { throw 'Install Python 3.10 or later first, then run capture.ps1 again.' }
        & $bootstrap @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or later is required.' }
        & $bootstrap @pythonArgs -m venv $envRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the capture environment.' }
    }
    $requirements = Join-Path $PSScriptRoot 'requirements.txt'
    $stamp = Join-Path $envRoot 'capture-requirements.sha256'
    $digest = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $installed = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
    if ($installed -ne $digest) {
        & $runner -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Could not install GUI capture dependencies.' }
        Set-Content -LiteralPath $stamp -Value $digest -Encoding ASCII
    }
    $captureArgs = @((Join-Path $PSScriptRoot 'capture.py'), '--exe', $Exe, '--output-root', $OutputRoot)
    if ($InspectOnly) { $captureArgs += '--inspect-only' }
    if ($Case) { $captureArgs += @('--case', $Case) }
    if ($Sweep) { $captureArgs += '--sweep' }
    if ($NextBatch) { $captureArgs += '--next-batch' }
    & $runner @captureArgs
    $captureExitCode = $LASTEXITCODE
    if ($captureExitCode -ne 0) {
        Write-Host ('[MMANA capture] Capture stopped with exit code ' + $captureExitCode) -ForegroundColor Red
        Write-Host ('[MMANA capture] Keep the results ZIP and launcher log in: ' + $OutputRoot)
    }
}
catch {
    $captureExitCode = 1
    Write-Host ('[MMANA capture] ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
}
finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null }
        catch { Write-Warning 'Could not finish the launcher transcript.' }
    }
    if ($captureExitCode -ne 0 -and -not $NoPause) {
        # Explorer-launched consoles must not vanish before the error can be read.
        try { Read-Host 'Capture stopped. Press Enter to close (use -NoPause for unattended runs)' | Out-Null }
        catch { Write-Warning 'No interactive console; inspect the launcher log and results directory.' }
    }
}
exit $captureExitCode
