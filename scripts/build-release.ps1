param(
    [switch]$SkipVerification
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install the development dependencies first."
}

Push-Location $root
try {
    if (-not $SkipVerification) {
        & (Join-Path $PSScriptRoot "verify.ps1")
        if ($LASTEXITCODE -ne 0) { throw "Verification failed." }
    }

    & $python -m build --outdir "dist\python"
    if ($LASTEXITCODE -ne 0) { throw "Python package build failed." }

    $version = (& $python -c "from welearn_studio import __version__; print(__version__)").Trim()
    if (-not $version) { throw "Package version could not be read." }
    $releaseRoot = Join-Path $root "dist\releases\$version"
    $appDist = Join-Path $releaseRoot "app"
    $workPath = Join-Path $root "build\pyinstaller\$version"
    $specPath = Join-Path $root "build\spec\$version"
    New-Item -ItemType Directory -Force -Path $appDist, $workPath, $specPath | Out-Null

    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name "WeLearn-Studio" `
        --paths "src" `
        --distpath $appDist `
        --workpath $workPath `
        --specpath $specPath `
        "src\welearn_studio\app.py"
    if ($LASTEXITCODE -ne 0) { throw "Windows application build failed." }

    $env:QT_QPA_PLATFORM = "offscreen"
    $env:WELEARN_STUDIO_SETTINGS_PATH = Join-Path $root "build\smoke-settings-$version.json"
    $env:WELEARN_STUDIO_NO_RESTORE = "1"
    $application = Join-Path $appDist "WeLearn-Studio\WeLearn-Studio.exe"
    $smokeTest = Start-Process `
        -FilePath $application `
        -ArgumentList "--smoke-test" `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($smokeTest.ExitCode -ne 0) {
        throw "Packaged application smoke test failed with exit code $($smokeTest.ExitCode)."
    }

    $applicationFiles = Join-Path $appDist "WeLearn-Studio\*"
    $versionedPortable = Join-Path $root "dist\WeLearn-Studio-$version-windows-x64.zip"
    $latestPortable = Join-Path $root "dist\WeLearn-Studio-windows-x64.zip"
    Compress-Archive -Path $applicationFiles -DestinationPath $versionedPortable -Force
    Copy-Item -LiteralPath $versionedPortable -Destination $latestPortable -Force
}
finally {
    Pop-Location
}

Write-Host "Release artifacts created under dist for version $version."
