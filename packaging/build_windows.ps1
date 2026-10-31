# Χτίζει το EXE τοπικά στα Windows (εναλλακτική του GitHub Actions).
# Χρήση, από τον φάκελο του έργου, σε PowerShell:  .\packaging\build_windows.ps1
# Χρειάζεται Python 3.12 (με tkinter, όπως στον επίσημο εγκαταστάτη) στο PATH.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m venv .venv-build
$py = '.\.venv-build\Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-build.txt
& $py -m pip install ".[dev]"

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Οι δοκιμές απέτυχαν. Δεν χτίστηκε EXE.' }

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name organotis `
    --distpath dist --workpath build\work --specpath build\spec packaging\launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Το χτίσιμο απέτυχε.' }

# Αυτοέλεγχος του ίδιου του EXE (το EXE «παραθύρου» δεν τυπώνει, γι' αυτό η αναφορά γράφεται σε αρχείο).
$report = Join-Path $env:TEMP 'organotis-selftest.txt'
if (Test-Path $report) { Remove-Item $report }
$p = Start-Process .\dist\organotis.exe -ArgumentList 'selftest', '--require-gui', '--out', $report -Wait -PassThru
if (-not (Test-Path $report)) { throw "Το EXE δεν έγραψε αναφορά (κωδικός $($p.ExitCode))." }
Get-Content $report -Encoding UTF8
if ($p.ExitCode -ne 0) { throw 'Ο αυτοέλεγχος του EXE απέτυχε.' }

$hash = (Get-FileHash .\dist\organotis.exe -Algorithm SHA256).Hash.ToLower()
"$hash  organotis.exe" | Out-File -Encoding ascii .\dist\organotis.exe.sha256
Write-Host ""
Write-Host "Έτοιμο: $(Resolve-Path .\dist\organotis.exe)"
Write-Host "SHA-256: $hash"
Write-Host "Επόμενο βήμα: docs\TEST_CHECKLIST.md (δοκίμασε πρώτα σε αντίγραφο φακέλου)."
