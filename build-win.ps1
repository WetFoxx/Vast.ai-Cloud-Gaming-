# build-win.ps1 — собрать VastGame для Windows (x64): dist\VastGame\VastGame.exe (папка с программой).
#
#   powershell -ExecutionPolicy Bypass -File build-win.ps1      (в Windows, из папки проекта с .venv)
#
# Внутри: свой Python со всеми библиотеками (PyInstaller), страница окна (ui\), OAuth-клиент Google
# (oauth_client.json — вне git, должен лежать в папке проекта) и rclone. Для запуска на другом компьютере не нужны
# ни Python, ни .venv, ни rclone. rclone — строго указанной версии с официального сайта, с проверкой контрольной
# суммы (при смене версии обновить обе строки ниже: https://downloads.rclone.org/<версия>/SHA256SUMS).
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$RcloneVer = 'v1.75.1'
$RcloneSha = '200eb602c126d82aa38b51e0f6b9ae837473ff99b51278d3f6f837574c494d6e'   # rclone-$RcloneVer-windows-amd64.zip
$Py = '.venv\Scripts\python.exe'

if (-not (Test-Path oauth_client.json)) { throw 'Нет oauth_client.json — без него новые пользователи не войдут в Google' }

# ---- rclone
$V = 'build\vendor-win'
New-Item -ItemType Directory -Force $V | Out-Null
$have = (Test-Path "$V\rclone.exe") -and ((& "$V\rclone.exe" version | Select-Object -First 1) -match "rclone $RcloneVer")
if (-not $have) {
    $zip = "$V\rclone-$RcloneVer-windows-amd64.zip"
    curl.exe -fsSL -o $zip "https://downloads.rclone.org/$RcloneVer/rclone-$RcloneVer-windows-amd64.zip"
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $RcloneSha) { throw 'rclone: контрольная сумма не совпала' }
    tar.exe -xf $zip -C $V
    Copy-Item "$V\rclone-$RcloneVer-windows-amd64\rclone.exe" $V -Force
}
& "$V\rclone.exe" version | Select-Object -First 1

# ---- программа
Remove-Item -Recurse -Force build\VastGame, dist\VastGame -ErrorAction SilentlyContinue
& $Py -X utf8 -m PyInstaller --noconfirm --clean --distpath dist vastgame-win.spec
if ($LASTEXITCODE) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }

# ---- самопроверка собранной программы (без окна, сети и ключей): модули, страница, rclone, режим UTF-8.
# У программы без консоли нет вывода — она пишет итог в файл VASTGAME_OUT
$out = Join-Path $env:TEMP 'vastgame-selftest.txt'
Remove-Item $out -ErrorAction SilentlyContinue
$env:VASTGAME_OUT = $out
$p = Start-Process 'dist\VastGame\VastGame.exe' -ArgumentList 'selftest' -Wait -PassThru
Remove-Item Env:VASTGAME_OUT
Get-Content $out -Encoding UTF8
if ($p.ExitCode) { throw "самопроверка не прошла (код $($p.ExitCode))" }
$size = [math]::Round(((Get-ChildItem dist\VastGame -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
"Готово: dist\VastGame\VastGame.exe ($size МБ)"
