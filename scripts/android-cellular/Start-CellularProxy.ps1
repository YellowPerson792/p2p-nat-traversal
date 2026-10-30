param(
    [ValidateRange(1,900)][int]$Seconds = 600,
    [switch]$DirectUsb,
    [switch]$OverlayUdp,
    [string]$PhoneAddress,
    [string]$PcAddress,
    [string]$ServerAddress,
    [string]$PeerAddress,
    [ValidateRange(1,65535)][int]$ServerUdpPort = 3478,
    [ValidateRange(1,65535)][int]$ServerControlPort = 8443,
    [ValidateRange(1,65535)][int]$LocalPort = 1080,
    [string]$Sdk = "$env:LOCALAPPDATA/Android/Sdk"
)
$ErrorActionPreference = 'Stop'
if ($OverlayUdp) {
    $DirectUsb = $true
    if (!$ServerAddress) { throw 'OverlayUdp requires ServerAddress.' }
}
if ($DirectUsb -and (!$PhoneAddress -or !$PcAddress)) { throw 'DirectUsb requires PhoneAddress and PcAddress from the current USB subnet.' }
if ($DirectUsb) {
    $addresses = @($PhoneAddress, $PcAddress)
    if ($OverlayUdp) {
        $addresses += $ServerAddress
        if ($PeerAddress) { $addresses += $PeerAddress }
    }
    foreach ($address in $addresses) {
        $parsed = [System.Net.IPAddress]::Parse($address)
        if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw 'DirectUsb requires literal IPv4 addresses.' }
    }
}
$adb = Join-Path $Sdk 'platform-tools/adb.exe'
$javac = (Get-Command javac -ErrorAction Stop).Source
$env:JAVA_HOME = Split-Path (Split-Path $javac)
$platform = Get-ChildItem "$Sdk/platforms" -Directory | Sort-Object { [int]($_.Name -replace '\D','') } -Descending | Select-Object -First 1
$build = Get-ChildItem "$Sdk/build-tools" -Directory | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
$root = Split-Path (Split-Path $PSScriptRoot)
$output = Join-Path $root '.local/android-cellular/pc-proxy'
New-Item -ItemType Directory -Force "$output/classes" | Out-Null
& $javac --release 8 -classpath "$($platform.FullName)/android.jar" -d "$output/classes" "$PSScriptRoot/CellularProxy.java" "$PSScriptRoot/CellularUdpBridge.java"
if ($LASTEXITCODE) { throw 'javac failed' }
$classes = @(Get-ChildItem "$output/classes" -Filter '*.class' | ForEach-Object FullName)
& "$($build.FullName)/d8.bat" --lib "$($platform.FullName)/android.jar" --min-api 26 --output "$output/proxy.jar" @classes
if ($LASTEXITCODE) { throw 'd8 failed' }
& $adb -d push "$output/proxy.jar" /data/local/tmp/overlay-cellular-proxy.jar
if ($LASTEXITCODE) { throw 'adb push failed' }
$stopFile = '/data/local/tmp/overlay-cellular-proxy.stop'
if ($OverlayUdp) { $stopFile = '/data/local/tmp/overlay-cellular-udp.stop' }
& $adb -d shell rm -f $stopFile
if ($LASTEXITCODE) { throw 'Cannot clear stop marker' }
$bindAddress = '127.0.0.1'; $allowedAddress = '127.0.0.1'
if ($DirectUsb) { $bindAddress = $PhoneAddress; $allowedAddress = $PcAddress }
else {
    & $adb -d forward --no-rebind "tcp:$LocalPort" tcp:49172
    if ($LASTEXITCODE) { throw 'Cannot reserve local ADB proxy port' }
}
try {
    Write-Host "Proxy runs for at most $Seconds seconds. Stop from another shell: adb -d shell touch $stopFile"
    if ($OverlayUdp) {
        $peer = if ($PeerAddress) { $PeerAddress } else { 'none' }
        & $adb -d shell "CLASSPATH=/data/local/tmp/overlay-cellular-proxy.jar app_process /system/bin CellularUdpBridge $Seconds $bindAddress $allowedAddress $ServerAddress $ServerUdpPort $ServerControlPort $peer $stopFile"
    } else {
        & $adb -d shell "CLASSPATH=/data/local/tmp/overlay-cellular-proxy.jar app_process /system/bin CellularProxy $Seconds $bindAddress $allowedAddress $stopFile"
    }
    if ($LASTEXITCODE) { throw 'Phone proxy failed' }
} finally {
    & $adb -d shell touch $stopFile
    if (!$DirectUsb) { & $adb -d forward --remove "tcp:$LocalPort" }
}
