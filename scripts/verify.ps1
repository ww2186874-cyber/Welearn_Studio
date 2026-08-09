$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install the project first."
}

Push-Location $root
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $python -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { throw "Static checks failed." }

    & $python -m ruff format --check src tests
    if ($LASTEXITCODE -ne 0) { throw "Formatting check failed." }

    & $python -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

    & $python -m compileall -q src tests
    if ($LASTEXITCODE -ne 0) { throw "Compilation failed." }

    git diff --check
    if ($LASTEXITCODE -ne 0) { throw "Git whitespace check failed." }
}
finally {
    Pop-Location
}

Write-Host "WeLearn Studio verification passed."
