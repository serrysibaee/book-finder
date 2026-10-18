# build.ps1 - build the standalone Windows console executable for BookFinder.
# Run from the project root on Windows 10/11 x64 with Python 3.11 installed:
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# Force UTF-8 so pip/PyInstaller don't crash printing non-ASCII paths
# (e.g. an Arabic "Documents" folder) to a cp1252 console.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Prefer the Windows launcher for 3.11; fall back to plain python.
$Py = Get-Command "py" -ErrorAction SilentlyContinue
if ($Py) { $PyCmd = @("py", "-3.11") } else { $PyCmd = @("python") }

$VenvDir = Join-Path $Root ".venv"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPy)) {
    Write-Host "==> Creating virtual environment..."
    if ($PyCmd.Length -gt 1) {
        & $PyCmd[0] $PyCmd[1] -m venv $VenvDir
    } else {
        & $PyCmd[0] -m venv $VenvDir
    }
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host "==> Installing dependencies + PyInstaller..."
& $VenvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit 1 }
& $VenvPy -m pip install -r (Join-Path $Root "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { exit 1 }

Push-Location $Root
try {
    Write-Host "==> Building onedir executable (desktop window)..."
    # --collect-all llama_cpp bundles the llama.cpp native library shipped
    # inside the package, which PyInstaller's static analysis can miss.
    & $VenvPy -m PyInstaller `
        --noconfirm --clean `
        --onedir --windowed `
        --name BookFinder `
        --collect-all llama_cpp `
        --hidden-import ddgs `
        --hidden-import llama_cpp `
        (Join-Path $Root "gui.py")
    if ($LASTEXITCODE -ne 0) { exit 1 }

    # Optional onefile build (uncomment if you want a single .exe; slower
    # startup because it unpacks native libraries to a temp folder each run):
    # & $VenvPy -m PyInstaller `
    #     --noconfirm --clean `
    #     --onefile --console `
    #     --name BookFinder-Onefile `
    #     --collect-all llama_cpp `
    #     --hidden-import ddgs `
    #     --hidden-import llama_cpp `
    #     (Join-Path $Root "main.py")
    # if ($LASTEXITCODE -ne 0) { exit 1 }

    Write-Host ""
    Write-Host "Build complete: $Root\dist\BookFinder\BookFinder.exe"
    Write-Host "The model is NOT bundled; it downloads to the Hugging Face cache on first run."
}
finally {
    Pop-Location
}
