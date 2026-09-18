# MultiFirm tool launcher.
#
# Python is chosen in this order:
#   1. $env:MULTIFIRM_PYTHON (used even if its esptool differs; multifirm.py then refuses device access)
#   2. tools\.venv\Scripts\python.exe
#   3. ..\M5StopWatch-UserDemo\.tools\idf-tools\python_env\*\Scripts\python.exe
# Candidates 2 and 3 are used only when they have the verified esptool version.
$ErrorActionPreference = 'Stop'
$required = '4.12.0'
$script = Join-Path $PSScriptRoot 'multifirm.py'

function Get-EsptoolVersion([string]$python) {
    $version = & $python -c "import esptool; print(esptool.__version__)" 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return "$version".Trim()
}

$python = $null
if ($env:MULTIFIRM_PYTHON) {
    if (-not (Test-Path $env:MULTIFIRM_PYTHON)) { throw "MULTIFIRM_PYTHON not found: $env:MULTIFIRM_PYTHON" }
    $python = $env:MULTIFIRM_PYTHON
    $version = Get-EsptoolVersion $python
    if ($version -ne $required) {
        Write-Warning "esptool $version in $python is not the verified $required"
    }
} else {
    $candidates = @(Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
    $userDemo = Join-Path $PSScriptRoot '..\..\M5StopWatch-UserDemo\.tools\idf-tools\python_env'
    if (Test-Path $userDemo) {
        $candidates += Get-ChildItem $userDemo -Directory | ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' }
    }
    foreach ($candidate in $candidates) {
        if ((Test-Path $candidate) -and ((Get-EsptoolVersion $candidate) -eq $required)) {
            $python = $candidate
            break
        }
    }
}
if (-not $python) {
    throw @"
No Python with esptool $required found. Set up the MultiFirm environment:
  py -3.11 -m venv "$PSScriptRoot\.venv"
  & "$PSScriptRoot\.venv\Scripts\python.exe" -m pip install -r "$PSScriptRoot\requirements.txt"
or set `$env:MULTIFIRM_PYTHON to a Python that has esptool $required.
"@
}

& $python $script @args
exit $LASTEXITCODE
