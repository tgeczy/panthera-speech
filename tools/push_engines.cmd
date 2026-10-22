<# : push_engines.cmd -- send Panthera's engine data from this PC to a phone or watch
@echo off
rem Double-click to run.  The batch lines are only this header: everything
rem below the closing comment mark is PowerShell, read and run by the line
rem that follows, so the file needs no execution-policy change and opens no
rem editor.  The PowerShell side sees these first lines as a block comment.
setlocal
set "PANTHERA_PUSH_SELF=%~f0"
powershell.exe -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:PANTHERA_PUSH_SELF); . ([ScriptBlock]::Create($t))"
exit /b
#>

# ---------------------------------------------------------------------------
# What this does, and why it is shaped this way.
#
# A watch has no file picker and no PC file window, so the only road into it
# is adb.  The release app cannot be handed files directly (it is not
# debuggable, so there is no run-as), but it already reads the folder a PC's
# file window shows -- the inbox, Android/data/com.pantheraspeech.tts/files/
# panthera-data -- and moves whatever generation it finds there into its own
# protected storage the next time it runs unlocked.  So this pushes each
# generation into the inbox and then asks the app to come forward.
#
# Three things follow from that move, and each is handled here:
#
#   * The app moves a generation as soon as it sees its MacinTalk file, and
#     would happily move half a push and replace good voices with it.  So a
#     generation goes up as `<gen>.pushing`, a name the app ignores, and is
#     renamed to `<gen>` only after every file on the device has been checked
#     against this PC by size.
#   * The move is a copy, so for a moment the device holds the generation
#     twice.  Free space is checked for that before anything is sent.
#   * The app logs one line when a move lands ("moved lion ... into protected
#     storage") or one saying why it could not; that line is what tells this
#     tool the work is done.
#
# Watches over Wi-Fi drop transfers.  Each file is its own push, a file
# already on the device at the right size is skipped, and a failed file is
# tried again after one reconnect -- so pressing Push again after a drop picks
# up where it stopped.  Nothing talks to the device in the background while a
# transfer runs: a Galaxy Watch's adb daemon wedges when it is hammered.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Package = 'com.pantheraspeech.tts'
$GenLabels = [ordered]@{ tiger = 'Tiger'; leopard = 'Leopard'; snowleopard = 'Snow Leopard'; lion = 'Lion' }
# Google's own addresses: the "latest" link its platform-tools page gives,
# and that page, for anyone who would rather download it by hand.
$PlatformToolsUrl = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'
$PlatformToolsPage = 'https://developer.android.com/tools/releases/platform-tools'
$OwnTools = Join-Path $env:LOCALAPPDATA 'Panthera\platform-tools'

# ---- finding things on this PC ---------------------------------------------

function Find-Adb {
    $cands = @()
    $onPath = Get-Command adb.exe -ErrorAction SilentlyContinue
    if ($onPath) { $cands += $onPath.Source }
    $here = Split-Path -Parent $env:PANTHERA_PUSH_SELF
    $cands += (Join-Path $here 'platform-tools\adb.exe')
    $cands += (Join-Path $OwnTools 'adb.exe')
    if ($env:ANDROID_HOME) { $cands += (Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe') }
    if ($env:ANDROID_SDK_ROOT) { $cands += (Join-Path $env:ANDROID_SDK_ROOT 'platform-tools\adb.exe') }
    $cands += (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe')
    foreach ($c in $cands) { if ($c -and (Test-Path -LiteralPath $c)) { return $c } }
    return $null
}

# Where the desktop keeps the data: NVDA's shared macintalk folder, then the
# SAPI installer's.  Only a suggestion; the person can browse anywhere.
function Get-DefaultDataRoot {
    $cands = @()
    if ($env:APPDATA) { $cands += (Join-Path $env:APPDATA 'nvda\macintalk') }
    if ($env:ProgramData) { $cands += (Join-Path $env:ProgramData 'macintalk') }
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
    return ''
}

# A generation is a folder named for it (any case: the PC may say "Lion")
# holding the engine in either layout the app reads, and the dictionary.
function Test-Generation([string]$dir) {
    $mac = Join-Path $dir 'Speech\Synthesizers\MacinTalk.SpeechSynthesizer\Contents\MacOS\MacinTalk'
    $flat = Join-Path $dir 'MacinTalk'
    $sd = Join-Path $dir 'SpeechDictionary.framework\Versions\A\SpeechDictionary'
    ((Test-Path -LiteralPath $mac -PathType Leaf) -or (Test-Path -LiteralPath $flat -PathType Leaf)) -and
        (Test-Path -LiteralPath $sd -PathType Leaf)
}

function Get-Generations([string]$root) {
    $found = @()
    if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) { return $found }
    # Pointed at one generation's own folder rather than the folder of all of
    # them: look one level up instead.
    $leaf = Split-Path -Leaf $root
    if ($GenLabels.Contains($leaf.ToLowerInvariant()) -and (Test-Generation $root)) {
        $root = Split-Path -Parent $root
    }
    foreach ($key in $GenLabels.Keys) {
        $dir = Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ieq $key } | Select-Object -First 1
        if (-not $dir -or -not (Test-Generation $dir.FullName)) { continue }
        $files = @(Get-ChildItem -LiteralPath $dir.FullName -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notin @('.DS_Store', 'desktop.ini', 'Thumbs.db') })
        $bytes = ($files | Measure-Object -Property Length -Sum).Sum
        if (-not $bytes) { $bytes = 0 }
        $voiceDir = Join-Path $dir.FullName 'Speech\Voices'
        if (-not (Test-Path -LiteralPath $voiceDir)) { $voiceDir = Join-Path $dir.FullName 'Voices' }
        $voices = @(Get-ChildItem -LiteralPath $voiceDir -Directory -Filter '*.SpeechVoice' -ErrorAction SilentlyContinue).Count
        $found += [pscustomobject]@{ Key = $key; Label = $GenLabels[$key]; Path = $dir.FullName;
                                     Bytes = [long]$bytes; Voices = $voices }
    }
    return $found
}

function Format-Size([double]$b) {
    if ($b -ge 1GB) { return ('{0:0.0} GB' -f ($b / 1GB)) }
    if ($b -ge 1MB) { return ('{0:0} MB' -f ($b / 1MB)) }
    return ('{0:0} KB' -f ($b / 1KB))
}

# ---- the background worker -------------------------------------------------
#
# Everything that talks to adb runs here, in its own runspace, so the window
# never stops answering while a 700 MB voice crosses Wi-Fi.  It reports
# through a queue the window drains on a timer.

$Worker = {
    param($sync, $task, $p)
    $ErrorActionPreference = 'Stop'

    function Say([string]$text) { $sync.Queue.Enqueue(@{ Kind = 'log'; Text = $text }) }
    function Progress([int]$pct, [string]$text) { $sync.Queue.Enqueue(@{ Kind = 'progress'; Value = $pct; Text = $text }) }
    function Size([double]$b) {
        if ($b -ge 1GB) { return ('{0:0.0} GB' -f ($b / 1GB)) }
        if ($b -ge 1MB) { return ('{0:0} MB' -f ($b / 1MB)) }
        return ('{0:0} KB' -f ($b / 1KB))
    }
    function Quote-Arg([string]$s) {
        if ($s -eq '') { return '""' }
        if ($s -notmatch '[\s"]') { return $s }
        $r = '"'; $bs = 0
        foreach ($c in $s.ToCharArray()) {
            if ($c -eq '\') { $bs++; continue }
            if ($c -eq '"') { $r += ('\' * ($bs * 2 + 1)) + '"'; $bs = 0; continue }
            $r += ('\' * $bs) + $c; $bs = 0
        }
        return $r + ('\' * ($bs * 2)) + '"'
    }
    # Single quotes for the device's own shell.
    function ShQ([string]$s) { return "'" + ($s -replace "'", "'\''") + "'" }

    function Adb([string[]]$argv, [int]$timeout = 0, [switch]$NoDevice) {
        $full = @()
        if (-not $NoDevice -and $p.Serial) { $full += @('-s', $p.Serial) }
        $full += $argv
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $p.Adb
        $psi.Arguments = (($full | ForEach-Object { Quote-Arg $_ }) -join ' ')
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
        $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
        $proc = [Diagnostics.Process]::Start($psi)
        $out = $proc.StandardOutput.ReadToEndAsync()
        $err = $proc.StandardError.ReadToEndAsync()
        $started = Get-Date
        while (-not $proc.WaitForExit(200)) {
            if ($sync.Cancel) { try { $proc.Kill() } catch {}; throw 'Cancelled.' }
            if ($timeout -gt 0 -and ((Get-Date) - $started).TotalSeconds -gt $timeout) {
                try { $proc.Kill() } catch {}
                return [pscustomobject]@{ Code = -1; Out = ''; Err = 'timed out' }
            }
        }
        $proc.WaitForExit()
        return [pscustomobject]@{ Code = $proc.ExitCode; Out = $out.Result; Err = $err.Result }
    }
    function Sh([string]$cmd, [int]$timeout = 60) { return Adb @('shell', $cmd) $timeout }

    function Device-State { (Adb @('get-state') 15).Out.Trim() }

    # One reconnect, never a loop: a watch's adb daemon wedges when it is
    # hammered with connection attempts.
    function Reconnect {
        if ($p.Serial -notmatch ':\d+$') { return $false }
        Say "The device dropped off; reconnecting once to $($p.Serial)."
        Adb @('disconnect', $p.Serial) 15 -NoDevice | Out-Null
        $r = Adb @('connect', $p.Serial) 30 -NoDevice
        return ($r.Out -match 'connected to')
    }

    function Remote-Sizes([string]$dir) {
        $map = @{}
        $r = Sh ("if [ -d {0} ]; then find {0} -type f -exec stat -c '%s %n' {{}} +; fi" -f (ShQ $dir)) 300
        foreach ($line in ($r.Out -split "`n")) {
            $line = $line.TrimEnd("`r")
            $i = $line.IndexOf(' ')
            if ($i -le 0) { continue }
            $map[$line.Substring($i + 1)] = [long]$line.Substring(0, $i)
        }
        return $map
    }

    function Free-Bytes([string]$dir) {
        $r = Sh ('df -k {0}' -f (ShQ $dir)) 30
        $lines = @($r.Out -split "`n" | Where-Object { $_.Trim() })
        if ($lines.Count -lt 2) { return -1 }
        $cols = @($lines[-1] -split '\s+' | Where-Object { $_ })
        if ($cols.Count -lt 4) { return -1 }
        return [long]$cols[3] * 1024
    }

    function Device-Time { (Sh "date +'%m-%d %H:%M:%S.000'" 15).Out.Trim() }

    try {
        switch ($task) {
        'devices' {
            $r = Adb @('devices', '-l') 60 -NoDevice
            $list = @()
            foreach ($line in ($r.Out -split "`n")) {
                $line = $line.Trim()
                if (-not $line -or $line -like 'List of devices*' -or $line -like '*daemon*') { continue }
                $cols = @($line -split '\s+')
                if ($cols.Count -lt 2) { continue }
                $model = ''
                foreach ($c in $cols) { if ($c -like 'model:*') { $model = $c.Substring(6) -replace '_', ' ' } }
                $list += [pscustomobject]@{ Serial = $cols[0]; State = $cols[1]; Model = $model }
            }
            $sync.Queue.Enqueue(@{ Kind = 'devices'; List = $list })
        }
        'info' {
            $state = Device-State
            if ($state -ne 'device') {
                if ($state -eq '') { $state = 'not answering' }
                Say "The device is $state. If it says unauthorized, look at its screen and allow debugging from this PC."
                break
            }
            $model = (Sh 'getprop ro.product.model' 15).Out.Trim()
            $release = (Sh 'getprop ro.build.version.release' 15).Out.Trim()
            $ver = (Sh "dumpsys package $($p.Package) | grep -m1 versionName" 30).Out.Trim() -replace '^versionName=', ''
            $free = Free-Bytes '/sdcard'
            $app = 'Panthera is not installed'
            if ($ver) { $app = "Panthera $ver" }
            $freeText = ''
            if ($free -ge 0) { $freeText = ", $(Size $free) free" }
            Say "$model, Android $release, $app$freeText."
            $sync.Queue.Enqueue(@{ Kind = 'info'; Installed = [bool]$ver })
        }
        'pair' {
            $r = Adb @('pair', $p.PairAddress, $p.PairCode) 60 -NoDevice
            $text = ($r.Out + ' ' + $r.Err).Trim()
            if ($text -match 'Successfully paired') { Say 'Paired. Now enter the address from the Wireless debugging screen itself and choose Connect.' }
            else { Say "Pairing did not work: $text" }
        }
        'connect' {
            $r = Adb @('connect', $p.Address) 45 -NoDevice
            $text = ($r.Out + ' ' + $r.Err).Trim()
            if ($text -match 'connected to') {
                Say "Connected to $($p.Address)."
                # adb's own discovery can hold a second link to the same watch,
                # and two links on one watch is how its daemon wedges.
                $d = Adb @('devices') 30 -NoDevice
                foreach ($line in ($d.Out -split "`n")) {
                    $s = ($line.Trim() -split '\s+')[0]
                    if ($s -like '*._adb-tls-connect._tcp*') { Adb @('disconnect', $s) 15 -NoDevice | Out-Null }
                }
                $sync.Queue.Enqueue(@{ Kind = 'connected'; Serial = $p.Address })
            } else { Say "Could not connect: $text" }
        }
        'wifi' {
            # The four settings that keep a Wear OS watch's Wi-Fi up while its
            # Bluetooth link to the phone is connected.  They persist.
            foreach ($c in @('settings put global cw_disable_wifimediator 1', 'settings put global auto_wifi 0',
                             'settings put global wifi_always_requested 1',
                             'settings put system cw_enable_wifi_when_charging 1')) {
                Sh $c 30 | Out-Null
            }
            Say 'Done: the watch keeps its Wi-Fi on while connected to the phone, and while charging.'
        }
        'getadb' {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $zip = Join-Path $env:TEMP 'panthera-platform-tools.zip'
            Say "Downloading Google's platform tools…"
            (New-Object Net.WebClient).DownloadFile($p.Url, $zip)
            $dest = Split-Path -Parent $p.Into
            if (Test-Path -LiteralPath $p.Into) { Remove-Item -LiteralPath $p.Into -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)
            Remove-Item -LiteralPath $zip -Force
            $adb = Join-Path $p.Into 'adb.exe'
            if (-not (Test-Path -LiteralPath $adb)) { throw 'The download did not contain adb.exe.' }
            # Google signs adb.exe.  Anything else is not used, whatever
            # the address said it came from.
            $sig = Get-AuthenticodeSignature -LiteralPath $adb
            if ($sig.Status -ne 'Valid' -or -not $sig.SignerCertificate -or
                $sig.SignerCertificate.Subject -notmatch '^CN=Google LLC, O=Google LLC,') {
                Remove-Item -LiteralPath $p.Into -Recurse -Force
                throw "The downloaded adb.exe is not signed by Google ($($sig.Status)), so it was deleted and will not be used."
            }
            Say "adb is ready, in $($p.Into). It is signed by Google."
            $sync.Queue.Enqueue(@{ Kind = 'adb'; Path = $adb })
        }
        'push' {
            $inbox = "/sdcard/Android/data/$($p.Package)/files/panthera-data"
            if ((Device-State) -ne 'device') { throw 'The device is not connected. Choose it again, or reconnect.' }
            if (-not (Sh "pm path $($p.Package)" 30).Out.Trim().StartsWith('package:')) {
                throw 'Panthera is not installed on this device. Install the app first.'
            }
            # The app makes its inbox when it opens; opening it once is the
            # surest way to have one that belongs to it.
            if ((Sh ("[ -d {0} ] && echo yes" -f (ShQ $inbox)) 15).Out.Trim() -ne 'yes') {
                Say 'Opening Panthera on the device once, so it makes its folder.'
                Sh 'input keyevent KEYCODE_WAKEUP' 15 | Out-Null
                Sh "am start -W -n $($p.Package)/.SettingsActivity" 60 | Out-Null
                Start-Sleep -Seconds 3
                if ((Sh ("[ -d {0} ] && echo yes" -f (ShQ $inbox)) 15).Out.Trim() -ne 'yes') {
                    Sh ('mkdir -p {0}' -f (ShQ $inbox)) 15 | Out-Null
                }
            }
            $grand = ($p.Gens | Measure-Object -Property Bytes -Sum).Sum
            $doneAll = [long]0
            $moved = @()
            foreach ($g in $p.Gens) {
                $staging = "$inbox/$($g.Key).pushing"
                $final = "$inbox/$($g.Key)"
                Say "$($g.Label): $(Size $g.Bytes) in $($g.Voices) voices."

                # The device holds it twice while the app copies it in.
                $have = Remote-Sizes $staging
                $already = [long]0; foreach ($v in $have.Values) { $already += $v }
                $free = Free-Bytes $inbox
                $need = 2 * $g.Bytes - $already + 64MB
                if ($free -ge 0 -and $free -lt $need) {
                    Say "  Skipped: it needs $(Size $need) free on the device for a moment, and there is $(Size $free)."
                    $doneAll += $g.Bytes
                    continue
                }

                $files = @(Get-ChildItem -LiteralPath $g.Path -Recurse -File -Force |
                    Where-Object { $_.Name -notin @('.DS_Store', 'desktop.ini', 'Thumbs.db') })
                $dirs = @(Get-ChildItem -LiteralPath $g.Path -Recurse -Directory -Force | ForEach-Object {
                    "$staging/" + ($_.FullName.Substring($g.Path.Length).TrimStart('\') -replace '\\', '/') })
                $dirs = @($staging) + $dirs
                for ($i = 0; $i -lt $dirs.Count; $i += 40) {
                    $chunk = $dirs[$i..([Math]::Min($i + 39, $dirs.Count - 1))]
                    $r = Sh ('mkdir -p ' + (($chunk | ForEach-Object { ShQ $_ }) -join ' ')) 60
                    if ($r.Code -ne 0) { throw "Could not make folders on the device: $($r.Out)$($r.Err)" }
                }

                $done = [long]0
                $skipped = 0
                foreach ($f in $files) {
                    if ($sync.Cancel) { throw 'Cancelled.' }
                    $rel = $f.FullName.Substring($g.Path.Length).TrimStart('\') -replace '\\', '/'
                    $remote = "$staging/$rel"
                    if ($have.ContainsKey($remote) -and $have[$remote] -eq $f.Length) {
                        $done += $f.Length; $skipped++
                    } else {
                        $ok = $false
                        for ($try = 1; $try -le 3 -and -not $ok; $try++) {
                            $r = Adb @('push', $f.FullName, $remote)
                            if ($r.Code -eq 0) { $ok = $true; break }
                            Say "  $rel did not go across ($(($r.Err + $r.Out).Trim())); trying again."
                            if ((Device-State) -ne 'device') {
                                if (-not (Reconnect)) { throw 'The device is gone. Reconnect it and press Push again: what already arrived is kept.' }
                            }
                        }
                        if (-not $ok) { throw "$rel would not copy. Press Push again to carry on from here." }
                        $done += $f.Length
                    }
                    $pct = 0
                    if ($grand -gt 0) { $pct = [int](100 * ($doneAll + $done) / $grand) }
                    Progress $pct "$($g.Label): $(Size $done) of $(Size $g.Bytes)"
                }
                if ($skipped) { Say "  $skipped files were already on the device and were kept." }

                # Every file there, at its size, before the app may see it.
                $there = Remote-Sizes $staging
                foreach ($f in $files) {
                    $rel = $f.FullName.Substring($g.Path.Length).TrimStart('\') -replace '\\', '/'
                    $remote = "$staging/$rel"
                    if (-not $there.ContainsKey($remote) -or $there[$remote] -ne $f.Length) {
                        throw "$rel did not arrive whole. Press Push again to send it."
                    }
                }
                Say "  All $($files.Count) files arrived whole."

                $since = Device-Time
                Sh ('rm -rf {0} && mv {1} {0}' -f (ShQ $final), (ShQ $staging)) 120 | Out-Null
                if ((Sh ("[ -d {0} ] && echo yes" -f (ShQ $final)) 15).Out.Trim() -ne 'yes') {
                    throw "Could not put $($g.Label) in place on the device."
                }
                Say "  Asking Panthera to move $($g.Label) into its own storage."
                Sh 'input keyevent KEYCODE_WAKEUP' 15 | Out-Null
                Sh "am start -f 0x10008000 -n $($p.Package)/.SettingsActivity" 60 | Out-Null

                $started = Get-Date; $nudged = $false; $result = $null
                while (-not $result) {
                    if ($sync.Cancel) { throw 'Cancelled.' }
                    Start-Sleep -Seconds 3
                    $log = (Sh "logcat -d -T '$since' -s PantheraEngine" 30).Out
                    foreach ($line in ($log -split "`n")) {
                        if ($line -match "moved $($g.Key) \(") { $result = 'ok' }
                        elseif ($line -match "$([regex]::Escape($g.Label)) could not be moved into protected storage: (.*)") {
                            $result = $Matches[1].Trim()
                        }
                    }
                    $mins = ((Get-Date) - $started).TotalMinutes
                    if (-not $result -and -not $nudged -and $mins -gt 1) {
                        Say '  Still waiting. If the device is locked, unlock it and open Panthera; the move starts then.'
                        $nudged = $true
                    }
                    if (-not $result -and $mins -gt 30) { throw "Panthera has not reported moving $($g.Label) after half an hour." }
                }
                if ($result -ne 'ok') { throw "Panthera could not move $($g.Label): $result" }
                # The app removes what it moved; if a file of ours was beyond
                # it, remove the rest so it is not moved a second time.
                Sh ('rm -rf {0}' -f (ShQ $final)) 120 | Out-Null
                Say "  $($g.Label) is in. Its voices are in the device's voice list now."
                $moved += $g.Label
                $doneAll += $g.Bytes
            }
            Progress 100 'Done.'
            if ($moved.Count) { $sync.Queue.Enqueue(@{ Kind = 'finished'; Text = "Sent $($moved -join ', ')." }) }
            else { $sync.Queue.Enqueue(@{ Kind = 'finished'; Text = 'Nothing was sent.' }) }
        }
        }
    } catch {
        $msg = $_.Exception.Message
        if ($msg -eq 'Cancelled.') { Say 'Stopped. Press Push again to carry on; what already arrived is kept.' }
        else { Say $msg; if ($task -eq 'push') { $sync.Queue.Enqueue(@{ Kind = 'failed'; Text = $msg }) } }
    } finally {
        $sync.Queue.Enqueue(@{ Kind = 'idle' })
    }
}

$sync = [hashtable]::Synchronized(@{
    Queue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[object]'
    Cancel = $false
})
$script:busy = $false
$script:adb = Find-Adb
$script:gens = @()
$script:serials = @()
$script:runspace = $null; $script:ps = $null

# ---- the window ------------------------------------------------------------

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Send Panthera voices to a phone or watch'
$form.ClientSize = New-Object System.Drawing.Size(640, 640)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

$script:tab = 0
function Add-Control($ctl, [int]$x, [int]$y, [int]$w, [int]$h, [string]$name = '') {
    $ctl.Location = New-Object System.Drawing.Point($x, $y)
    $ctl.Size = New-Object System.Drawing.Size($w, $h)
    $ctl.TabIndex = $script:tab; $script:tab++
    if ($name) { $ctl.AccessibleName = $name }
    $form.Controls.Add($ctl)
    return $ctl
}
function New-Label([string]$text, [int]$x, [int]$y, [int]$w) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.AutoSize = $false
    Add-Control $l $x $y $w 18 | Out-Null
    return $l
}

New-Label 'Speech data &folder (the one that holds tiger, leopard, snowleopard, lion):' 12 12 616 | Out-Null
$folder = Add-Control (New-Object System.Windows.Forms.TextBox) 12 32 520 23 'Speech data folder'
$browse = Add-Control (New-Object System.Windows.Forms.Button) 540 31 88 25 'Browse for the speech data folder'
$browse.Text = '&Browse…'

New-Label '&Generations to send:' 12 64 616 | Out-Null
$genList = Add-Control (New-Object System.Windows.Forms.CheckedListBox) 12 84 616 76 'Generations to send'
$genList.CheckOnClick = $true

New-Label '&Device:' 12 172 616 | Out-Null
$device = Add-Control (New-Object System.Windows.Forms.ComboBox) 12 192 520 23 'Device'
$device.DropDownStyle = 'DropDownList'
$refresh = Add-Control (New-Object System.Windows.Forms.Button) 540 191 88 25 'Refresh the device list'
$refresh.Text = '&Refresh'
$deviceInfo = New-Label 'No device chosen yet. A phone on a USB cable shows up here by itself.' 12 220 616

New-Label 'Wi-Fi &address, from the Wireless debugging screen (like 192.168.1.20:37449):' 12 248 616 | Out-Null
$address = Add-Control (New-Object System.Windows.Forms.TextBox) 12 268 520 23 'Wi-Fi address and port'
$connect = Add-Control (New-Object System.Windows.Forms.Button) 540 267 88 25 'Connect over Wi-Fi'
$connect.Text = '&Connect'

New-Label 'First time only: from "Pair device with pairing code", its address and the six-digit code.' 12 300 616 | Out-Null
New-Label 'Pa&iring address:' 12 322 200 | Out-Null
$pairAddress = Add-Control (New-Object System.Windows.Forms.TextBox) 12 342 300 23 'Pairing address and port'
New-Label 'Pairing c&ode:' 322 322 200 | Out-Null
$pairCode = Add-Control (New-Object System.Windows.Forms.TextBox) 322 342 210 23 'Pairing code'
$pair = Add-Control (New-Object System.Windows.Forms.Button) 540 341 88 25 'Pair with the device'
$pair.Text = '&Pair'

$wifi = Add-Control (New-Object System.Windows.Forms.Button) 12 378 300 25 "Keep a watch's Wi-Fi on while it is connected to the phone"
$wifi.Text = "Keep a &watch's Wi-Fi on…"
$getAdb = Add-Control (New-Object System.Windows.Forms.Button) 322 378 306 25 "Download Google's adb"
$getAdb.Text = "Download Google's &adb…"

$push = Add-Control (New-Object System.Windows.Forms.Button) 12 416 300 32 'Push engines'
$push.Text = 'Push &engines'
$cancel = Add-Control (New-Object System.Windows.Forms.Button) 322 416 150 32 'Stop'
$cancel.Text = '&Stop'; $cancel.Enabled = $false
$close = Add-Control (New-Object System.Windows.Forms.Button) 478 416 150 32 'Close'
$close.Text = 'Close'

$bar = Add-Control (New-Object System.Windows.Forms.ProgressBar) 12 458 616 20 'Progress'
$status = New-Label '' 12 482 616
New-Label '&Log:' 12 504 616 | Out-Null
$log = Add-Control (New-Object System.Windows.Forms.TextBox) 12 524 616 104 'Log'
$log.Multiline = $true; $log.ReadOnly = $true; $log.ScrollBars = 'Vertical'
$form.CancelButton = $close

function Write-Log([string]$text) {
    $log.AppendText($text + "`r`n")
}

function Set-Busy([bool]$on) {
    $script:busy = $on
    foreach ($c in @($browse, $folder, $genList, $device, $refresh, $address, $connect, $pairAddress, $pairCode, $pair, $wifi, $getAdb, $push)) {
        $c.Enabled = -not $on
    }
    $cancel.Enabled = $on
}

function Start-Task([string]$task, $params) {
    if ($script:busy) { return }
    if (-not $script:adb -and $task -ne 'getadb') {
        Write-Log "adb is not on this PC yet. Choose Download Google's adb first."
        return
    }
    $params.Adb = $script:adb
    $params.Package = $Package
    $sync.Cancel = $false
    Set-Busy $true
    $script:runspace = [runspacefactory]::CreateRunspace()
    $script:runspace.ApartmentState = 'MTA'
    $script:runspace.Open()
    $script:ps = [powershell]::Create()
    $script:ps.Runspace = $script:runspace
    $script:ps.AddScript($Worker).AddArgument($sync).AddArgument($task).AddArgument($params) | Out-Null
    $script:handle = $script:ps.BeginInvoke()
}

function Selected-Serial {
    $i = $device.SelectedIndex
    if ($i -lt 0 -or $i -ge $script:serials.Count) { return $null }
    return $script:serials[$i]
}

function Load-Generations {
    $script:loadedFolder = $folder.Text.Trim()
    $genList.Items.Clear()
    $script:gens = @(Get-Generations $folder.Text.Trim())
    foreach ($g in $script:gens) {
        [void]$genList.Items.Add("$($g.Label): $($g.Voices) voices, $(Format-Size $g.Bytes)", $false)
    }
    if ($script:gens.Count -eq 0 -and $folder.Text.Trim()) {
        Write-Log 'No generations found in that folder. Choose the folder that holds tiger, leopard, snowleopard and lion.'
    }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 150
$timer.Add_Tick({
    $msg = $null
    while ($sync.Queue.TryDequeue([ref]$msg)) {
        switch ($msg.Kind) {
            'log' { Write-Log $msg.Text }
            'progress' { $bar.Value = [Math]::Max(0, [Math]::Min(100, $msg.Value)); $status.Text = $msg.Text }
            'devices' {
                $keep = Selected-Serial
                if ($script:wantSerial) { $keep = $script:wantSerial; $script:wantSerial = $null }
                $device.Items.Clear(); $script:serials = @()
                foreach ($d in $msg.List) {
                    $label = $d.Serial
                    if ($d.Model) { $label = "$($d.Model) ($($d.Serial))" }
                    if ($d.State -ne 'device') { $label += " - $($d.State)" }
                    [void]$device.Items.Add($label); $script:serials += $d.Serial
                }
                if ($script:serials.Count -eq 0) { $deviceInfo.Text = 'No device found. Plug in a phone, or connect over Wi-Fi below.' }
                else {
                    $i = [Array]::IndexOf($script:serials, $keep)
                    if ($i -lt 0) { $i = 0 }
                    $device.SelectedIndex = $i
                }
            }
            'connected' { $script:pendingSerial = $msg.Serial }
            'adb' { $script:adb = $msg.Path }
            'finished' {
                [void][System.Windows.Forms.MessageBox]::Show($form, $msg.Text, 'Panthera', 'OK', 'Information')
            }
            'failed' {
                [void][System.Windows.Forms.MessageBox]::Show($form, $msg.Text, 'Panthera', 'OK', 'Warning')
            }
            'idle' {
                try { $script:ps.EndInvoke($script:handle) } catch {}
                $script:ps.Dispose(); $script:runspace.Close()
                Set-Busy $false
                if ($script:pendingSerial) {
                    $s = $script:pendingSerial; $script:pendingSerial = $null
                    $script:wantSerial = $s
                    Start-Task 'devices' @{}
                } elseif ($script:wantInfo) {
                    $script:wantInfo = $false
                    $s = Selected-Serial
                    if ($s) { Start-Task 'info' @{ Serial = $s } }
                }
            }
        }
    }
})

$device.Add_SelectedIndexChanged({
    $s = Selected-Serial
    if (-not $s) { return }
    $deviceInfo.Text = "Chosen: $($device.SelectedItem)"
    if ($script:busy) { $script:wantInfo = $true } else { Start-Task 'info' @{ Serial = $s } }
})

$browse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Choose the folder that holds tiger, leopard, snowleopard and lion'
    if ($folder.Text -and (Test-Path -LiteralPath $folder.Text)) { $dlg.SelectedPath = $folder.Text }
    if ($dlg.ShowDialog($form) -eq 'OK') { $folder.Text = $dlg.SelectedPath; Load-Generations }
})
$folder.Add_Leave({ if ($folder.Text.Trim() -ne $script:loadedFolder) { Load-Generations } })
$refresh.Add_Click({ Start-Task 'devices' @{} })
$connect.Add_Click({
    $a = $address.Text.Trim()
    if ($a -notmatch '^[\w\.\-]+:\d+$') { Write-Log 'The address looks like 192.168.1.20:37449: the numbers and the port after the colon.'; return }
    Start-Task 'connect' @{ Address = $a }
})
$pair.Add_Click({
    $a = $pairAddress.Text.Trim(); $c = $pairCode.Text.Trim()
    if ($a -notmatch '^[\w\.\-]+:\d+$' -or $c -notmatch '^\d{6}$') {
        Write-Log 'Pairing needs the address with its port, and the six-digit code, both from the "Pair device with pairing code" screen.'; return
    }
    Start-Task 'pair' @{ PairAddress = $a; PairCode = $c }
})
$wifi.Add_Click({
    $s = Selected-Serial
    if (-not $s) { Write-Log 'Choose the watch first.'; return }
    $answer = [System.Windows.Forms.MessageBox]::Show($form,
        "This changes four settings on the watch so its Wi-Fi stays on while it is connected to the phone, and while charging. It uses more battery, and it stays that way until you change it.`r`n`r`nGo ahead?",
        'Panthera', 'YesNo', 'Question')
    if ($answer -eq 'Yes') { Start-Task 'wifi' @{ Serial = $s } }
})
$getAdb.Add_Click({
    $answer = [System.Windows.Forms.MessageBox]::Show($form,
        "adb is part of Google's Android platform tools. Choose Yes to download them from Google's own server, dl.google.com, into`r`n$OwnTools`r`nThe download is checked to be signed by Google before it is used.`r`n`r`nChoose No to open Google's download page instead. If you download them yourself, unzip the platform-tools folder next to this file and open this again.",
        'Panthera', 'YesNoCancel', 'Question')
    if ($answer -eq 'Yes') { Start-Task 'getadb' @{ Url = $PlatformToolsUrl; Into = $OwnTools } }
    elseif ($answer -eq 'No') { Start-Process $PlatformToolsPage }
})
$push.Add_Click({
    $s = Selected-Serial
    if (-not $s) { Write-Log 'Choose a device first.'; return }
    $chosen = @()
    for ($i = 0; $i -lt $genList.Items.Count; $i++) { if ($genList.GetItemChecked($i)) { $chosen += $script:gens[$i] } }
    if ($chosen.Count -eq 0) { Write-Log 'Tick at least one generation to send.'; return }
    $bar.Value = 0
    Write-Log "Sending $(($chosen | ForEach-Object { $_.Label }) -join ', ') to $($device.SelectedItem)."
    Start-Task 'push' @{ Serial = $s; Gens = $chosen }
})
$cancel.Add_Click({ $sync.Cancel = $true; Write-Log 'Stopping…' })
$close.Add_Click({ $form.Close() })
$form.Add_FormClosing({
    if ($script:busy) {
        $answer = [System.Windows.Forms.MessageBox]::Show($form, 'Something is still being sent. Stop it and close?', 'Panthera', 'YesNo', 'Warning')
        if ($answer -ne 'Yes') { $_.Cancel = $true; return }
        $sync.Cancel = $true
    }
})

$form.Add_Shown({
    $folder.Text = Get-DefaultDataRoot
    Load-Generations
    if ($script:adb) {
        Write-Log "Using adb from $script:adb"
        Start-Task 'devices' @{}
    } else {
        Write-Log "adb was not found on this PC. Choose Download Google's adb to fetch it."
    }
    $folder.Focus()
})

$timer.Start()
[void]$form.ShowDialog()
$timer.Stop()
