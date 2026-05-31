$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $RootDir "course_notes_workflow.py"
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $PythonBin = $VenvPython
} elseif ($env:VIRTUAL_ENV) {
    $ActiveVenvPython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    if (Test-Path $ActiveVenvPython) {
        $PythonBin = $ActiveVenvPython
    }
}

if (-not $PythonBin) {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCmd) {
        $PythonBin = $PythonCmd.Source
    }
}

if (-not $PythonBin) {
    $PyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($PyCmd) {
        $PythonBin = $PyCmd.Source
    }
}

if (-not $PythonBin) {
    # Keep this script ASCII-only so Windows PowerShell 5.1 does not misread
    # UTF-8 source text on systems that still default to legacy code pages.
    throw "No usable Python was found. Activate a virtual environment first, or create $RootDir\.venv."
}

if ($PythonBin.ToLower().EndsWith("py.exe")) {
    & $PythonBin -3 $ScriptPath @args
} else {
    & $PythonBin $ScriptPath @args
}
