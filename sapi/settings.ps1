param([switch]$RegisterVoices,[switch]$UnregisterVoices,[string]$GenerationList,[string]$DataRoot,
      [switch]$MigrateData,[string]$MigrateFrom,[switch]$ShowMigrationPlan,[string]$MirrorSettings)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$stage = Split-Path -Parent $MyInvocation.MyCommand.Path
$settingsScript = $PSCommandPath
$clsid = '{C1F7FC55-3512-4F5D-A6EB-F53220BE4693}'
$Generations = @($GenerationList -split ',' | Where-Object { $_ })

# Where the MacinTalk data lives.  In order: the folder the user chose
# (remembered in HKCU, and passed explicitly through the elevated
# re-invocation, whose HKCU may not be this user's); the folder set for the
# *machine* (HKLM, which is the only one of the two a service account can
# read); NVDA's own shared macintalk folder, so an NVDA user registers SAPI
# voices from the data they already extracted rather than extracting
# everything twice; and a standalone default under %ProgramData%, falling
# back to the per-user folder earlier versions used.
#
# Explicit choices outrank defaults and this user outranks the machine --
# the same ranking `pantheratrees.sapi_roots` encodes on the NVDA side, and
# the two have to agree or a person's data is found by one and not the other.
$dataPrefKey = 'HKCU:\Software\Panthera SAPI'
$machinePrefPath = 'Software\Panthera SAPI'

#: %ProgramData%, named rather than hard-coded because a Windows install is
#: not obliged to put it on C:.
function Get-CommonRoot {
    $common = if ($env:ProgramData) { $env:ProgramData } else { $env:ALLUSERSPROFILE }
    if ($common) { Join-Path $common 'macintalk-data' } else { $null }
}

# The machine-wide DataPath, read from and written to **both registry views**.
#
# `HKLM\Software` is redirected under WOW64 and `HKCU\Software` is not, so
# this is a trap that arrives with the machine-wide key and did not exist
# before it: a 64-bit PowerShell writing through `Set-ItemProperty` lands in
# the 64-bit view alone, where 32-bit NVDA and the 32-bit engine DLL -- both
# of which read `Wow6432Node` -- will never see it.  The whole feature would
# ship dead and every test would still pass.  `Add-VoiceTokens` already
# writes tokens through both views for exactly this reason; this is the same
# dance for the same reason.
function Get-MachineDataPath {
    foreach ($view in 'Registry64','Registry32') {
        try {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
            $key = $base.OpenSubKey($machinePrefPath)
            if ($key) {
                $value = $key.GetValue('DataPath'); $key.Dispose(); $base.Dispose()
                if ($value) { return [string]$value }
            } else { $base.Dispose() }
        } catch {}
    }
    return $null
}
function Set-MachineDataPath([string]$path) {
    foreach ($view in 'Registry32','Registry64') {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
        $key = $base.CreateSubKey($machinePrefPath)
        $key.SetValue('DataPath',$path,'String')
        $key.Dispose(); $base.Dispose()
    }
}

#: The settings the engine DLL reads, in one place so the mirror below cannot
#: drift from the checkboxes above it.  Tool state -- which generations were
#: declined, whether the migration was declined -- is deliberately not here:
#: it is this person's business and means nothing to another account.
$SettingNames = @('AcceptCommands','Phrasing','ExpandAbbreviations','RateBoost',
                  'Inflection','NumberStyle','Diagnostics')

# The engine reads these from HKCU and falls back to HKLM per value, because a
# voice speaking under a service account -- the sign-in screen -- has an HKCU
# holding nothing anybody chose.  Mirroring happens on the elevated trips this
# tool already makes, and the values travel as an argument rather than being
# re-read on the other side: the elevated process's HKCU belongs to whichever
# account answered the prompt, which need not be this one.  Same reason
# -DataRoot has always been passed rather than resolved twice.
function Set-MachineSettings([string]$pairs) {
    if (!$pairs) { return }
    foreach ($view in 'Registry32','Registry64') {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
        $key = $base.CreateSubKey($machinePrefPath)
        foreach ($pair in @($pairs -split ';' | Where-Object { $_ })) {
            $name,$value = $pair -split '=',2
            if ($name -notin $SettingNames) { continue }
            # A number is a DWORD and a word is a string, which is exactly the
            # split the DLL makes between setting_dword and setting_string.
            if ($value -match '^\d+$') { $key.SetValue($name,[int]$value,'DWord') }
            else { $key.SetValue($name,[string]$value,'String') }
        }
        $key.Dispose(); $base.Dispose()
    }
}

function Resolve-DataRoot {
    if ($DataRoot) { return $DataRoot }
    try {
        $saved = (Get-ItemProperty -Path $dataPrefKey -Name DataPath -ErrorAction Stop).DataPath
        if ($saved -and (Test-Path -LiteralPath $saved)) { return $saved }
    } catch {}
    $machine = Get-MachineDataPath
    if ($machine -and (Test-Path -LiteralPath $machine)) { return $machine }
    $perUser = $null
    if ($env:APPDATA) {
        $nvda = Join-Path $env:APPDATA 'nvda\macintalk'
        if (Test-Path -LiteralPath $nvda) { return $nvda }
    }
    # **NVDA's folder name, machine-wide.**  `%ProgramData%\macintalk` rather
    # than `macintalk-data`: the add-on looks there now, so somebody who moved
    # NVDA's folder to %ProgramData% has their data in a place this tool could
    # see and, until this line, did not -- one word apart, exactly the way the
    # add-on's own `find_tree` missed it.
    #
    # It matters more here than there, because the add-on searches on every
    # start and these tokens do not: each of the 96 carries a DataPath written
    # once, at registration.  Move the folder by hand and every token still
    # names the old one, SAPI dispatches into the engine, and the engine finds
    # nothing -- which sounds exactly like silence and logs like success.
    $commonNvda = if ($env:ProgramData) { Join-Path $env:ProgramData 'macintalk' }
                  elseif ($env:ALLUSERSPROFILE) { Join-Path $env:ALLUSERSPROFILE 'macintalk' }
    if ($commonNvda -and (Test-Path -LiteralPath $commonNvda)) { return $commonNvda }
    if ($env:APPDATA) {
        $perUser = Join-Path $env:APPDATA 'macintalk-data'
        if (Test-Path -LiteralPath $perUser) { return $perUser }
    }
    $common = Get-CommonRoot
    if ($common -and (Test-Path -LiteralPath $common)) { return $common }
    # Nothing exists yet, so this is a fresh install choosing where to put
    # things -- and it chooses the *per-user* folder, not %ProgramData%.
    #
    # That looks backwards on the night ProgramData was added, and it is not.
    # A standard user can create a folder under %ProgramData% and write into
    # it, which is exactly what makes it the wrong place to extract to
    # unelevated: the folder they create inherits
    # `BUILTIN\Users:(CI)(WD,AD,WEA,WA)`, so every other account on the
    # machine can write into a tree whose Mach-O this host maps and executes,
    # and which NVDA reads as SYSTEM on the sign-in screen.
    #
    # The machine-wide folder is reached the one way that can lock it down on
    # arrival: the migration offer, which is elevated and sets an explicit ACL.
    # Anybody an administrator has already migrated finds it above, through
    # HKLM, and shares it.
    if ($perUser) { return $perUser }
    $common = Get-CommonRoot
    if ($common) { return $common }
    $null
}
$data = Resolve-DataRoot

# One row per generation, in one place, so a generation cannot be present in
# the extractor and missing from the list next to it.  Folder is what
# extract.py writes: the generation key, title-cased -- which is why Snow
# Leopard's folder is 'Snowleopard'.
$GenerationTable = @(
    [pscustomobject]@{ Folder='Tiger';       Label='Tiger';        Item='Tiger - Mac OS X 10.4' }
    [pscustomobject]@{ Folder='Leopard';     Label='Leopard';      Item='Leopard - Mac OS X 10.5' }
    [pscustomobject]@{ Folder='Snowleopard'; Label='Snow Leopard'; Item='Snow Leopard - Mac OS X 10.6' }
    [pscustomobject]@{ Folder='Lion';        Label='Lion';         Item='Lion - Mac OS X 10.7' }
)

# No folders are created here on purpose: the resolved root may be NVDA's
# own macintalk tree, and this tool is a reader of that arrangement, not a
# decorator of it.  The root is created where something is actually written
# -- extraction -- and nowhere else.

function Get-Voices {
    $rows = @()
    foreach ($generation in $GenerationTable) {
        $folder = Join-Path $data "$($generation.Folder)\Speech\Voices"
        if (Test-Path -LiteralPath $folder) {
            Get-ChildItem -LiteralPath $folder -Directory -Filter '*.SpeechVoice' | Sort-Object Name | ForEach-Object {
                $voiceName = $_.Name.Substring(0, $_.Name.Length - '.SpeechVoice'.Length)
                $rows += [pscustomobject]@{ Generation=$generation.Folder; Label=$generation.Label; Voice=$voiceName }
            }
        }
    }
    $rows
}

# --- moving the data somewhere every account can read ---------------------
# The SAPI data moves to %ProgramData%; NVDA's does not.  That looks
# inconsistent and is not: a portable NVDA copy carries its own configuration
# folder with it, so data kept inside that folder travels and data outside it
# is silently lost -- while SAPI has no portable copy to protect and every
# account on the machine needs to read one copy.  The NVDA driver *adds*
# %ProgramData% to the places it looks rather than moving anything.
#
# Exactly one arrangement is moved: the standalone per-user default,
# %APPDATA%\macintalk-data.  A folder somebody chose by hand is their choice
# and stays where they put it, and NVDA's macintalk folder is moved by
# nothing, ever -- taking it out of NVDA's configuration directory is exactly
# what breaks speech on the sign-in screen, where NVDA reads a copy of that
# directory and nothing else.
function Get-ComparablePath([string]$path) {
    if (!$path) { return '' }
    try { return ([System.IO.Path]::GetFullPath($path)).TrimEnd('\').ToLowerInvariant() }
    catch { return $path.TrimEnd('\').ToLowerInvariant() }
}
function Test-SamePath([string]$a, [string]$b) {
    $x = Get-ComparablePath $a
    return ($x -ne '' -and $x -eq (Get-ComparablePath $b))
}

function Get-MigrationPlan {
    $common = Get-CommonRoot
    $plan = [pscustomobject]@{ Action='none'; Reason=''; From=$data; To=$common }
    if (!$common) {
        $plan.Reason = 'this machine has no ProgramData folder'; return $plan
    }
    if (Test-SamePath $data $common) {
        $plan.Action='done'; $plan.Reason='the data is already in the machine-wide folder'; return $plan
    }
    if ($env:APPDATA) {
        if (Test-SamePath $data (Join-Path $env:APPDATA 'nvda\macintalk')) {
            $plan.Action='nvda'
            $plan.Reason='the data belongs to NVDA and moving it would break speech on the sign-in screen'
            return $plan
        }
        if (!(Test-SamePath $data (Join-Path $env:APPDATA 'macintalk-data'))) {
            $plan.Action='chosen'; $plan.Reason='this folder was chosen deliberately'; return $plan
        }
    } else {
        $plan.Action='chosen'; $plan.Reason='this folder was chosen deliberately'; return $plan
    }
    if (!(Test-Path -LiteralPath $data) -or !(@(Get-Voices).Count)) {
        $plan.Reason='there is no voice data to move'; return $plan
    }
    $plan.Action='migrate'; $plan.Reason='the data is in a folder only this account can read'
    $plan
}

function Remove-VoiceTokens([string[]]$SelectedGenerations) {
    foreach ($hive in 'CurrentUser','LocalMachine') {
        foreach ($view in 'Registry32','Registry64') {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
            $root = $base.CreateSubKey('Software\Microsoft\Speech\Voices\Tokens')
            @($root.GetSubKeyNames()) | Where-Object {
                $name=$_
                @($SelectedGenerations | Where-Object { $name -like "Panthera_$($_)_*" }).Count -gt 0
            } | ForEach-Object { $root.DeleteSubKeyTree($_,$false) }
            $root.Dispose(); $base.Dispose()
        }
    }
}

function Add-VoiceTokens([string[]]$SelectedGenerations) {
    Remove-VoiceTokens $SelectedGenerations
    $voices = @(Get-Voices | Where-Object { $_.Generation -in $SelectedGenerations })
    foreach ($view in 'Registry32','Registry64') {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
        $root = $base.CreateSubKey('Software\Microsoft\Speech\Voices\Tokens')
        foreach ($voice in $voices) {
            $name = 'Panthera_{0}_{1}' -f $voice.Generation,($voice.Voice -replace ' ','_')
            # Some SAPI clients key their voice list by Attributes\Name (or by
            # the convenient VoiceName value) instead of by the token ID.  A
            # bare "Alex" therefore made Tiger, Leopard, Snow Leopard and Lion
            # look like one voice even though their token keys were distinct.
            # Keep the engine's bundle name separately and make every value an
            # application might use as an identity generation-qualified.
            $displayName = '{0} ({1})' -f $voice.Voice,$voice.Label
            $key = $root.CreateSubKey($name)
            $key.SetValue('',$displayName)
            $key.SetValue('CLSID',$clsid); $key.SetValue('VoiceName',$displayName)
            $key.SetValue('EngineVoiceName',$voice.Voice)
            $key.SetValue('Generation',$voice.Generation); $key.SetValue('DataPath',$data)
            $attributes = $key.CreateSubKey('Attributes')
            $attributes.SetValue('Name',$displayName); $attributes.SetValue('Vendor','Panthera Speech')
            $attributes.SetValue('Version',$voice.Label)
            $attributes.SetValue('Language','409'); $attributes.SetValue('Gender','Neutral')
            $attributes.Dispose(); $key.Dispose()
        }
        $root.Dispose(); $base.Dispose()
    }
    $voices.Count
}

# The DataPath one registered Panthera token carries, or $null.  They all
# carry the same one, written at registration, so the first is the answer.
function Get-TokenDataPath {
    foreach ($view in 'Registry32','Registry64') {
        try {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
            $root = $base.OpenSubKey('Software\Microsoft\Speech\Voices\Tokens')
            if ($root) {
                foreach ($name in @($root.GetSubKeyNames())) {
                    if ($name -notlike 'Panthera_*') { continue }
                    $key = $root.OpenSubKey($name)
                    if ($key) {
                        $value = $key.GetValue('DataPath'); $key.Dispose()
                        if ($value) {
                            $root.Dispose(); $base.Dispose(); return [string]$value
                        }
                    }
                }
                $root.Dispose()
            }
            $base.Dispose()
        } catch {}
    }
    return $null
}

function Test-AnyPantheraTokens {
    foreach ($view in 'Registry32','Registry64') {
        $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
        $root=$base.OpenSubKey('Software\Microsoft\Speech\Voices\Tokens')
        if ($root -and @($root.GetSubKeyNames() | Where-Object { $_ -like 'Panthera_*' }).Count) {
            $root.Dispose();$base.Dispose();return $true
        }
        if($root){$root.Dispose()};$base.Dispose()
    }
    return $false
}

if ($ShowMigrationPlan) {
    # What the migration offer would decide, without deciding it.  The move
    # itself needs elevation, a machine with data on it and a registry to
    # write; the classification needs none of those, so it is the part a test
    # can hold still.  Pass -DataRoot to pin the resolved root.
    $plan = Get-MigrationPlan
    Write-Output ('plan: {0}' -f $plan.Action)
    Write-Output ('from: {0}' -f $plan.From)
    Write-Output ('to: {0}' -f $plan.To)
    Write-Output ('reason: {0}' -f $plan.Reason)
    exit 0
}

if ($MigrateData) {
    # Elevated, and told both ends explicitly: -MigrateFrom is the folder
    # being emptied and -DataRoot is where it lands, which also makes $data
    # the destination so Add-VoiceTokens registers against the new root
    # unchanged.  Neither may be read from this process's own HKCU or
    # %APPDATA%, which belong to whichever account answered the elevation
    # prompt and need not be the account whose data this is.
    if (!$MigrateFrom -or !$DataRoot) { Write-Error 'Both -MigrateFrom and -DataRoot are required.'; exit 2 }
    if (!(Test-Path -LiteralPath $MigrateFrom)) { Write-Error 'The folder to move is not there.'; exit 3 }
    try {
        New-Item -ItemType Directory -Force -Path $DataRoot -ErrorAction Stop | Out-Null
        # One generation at a time rather than the folder whole, so a run
        # that stopped halfway -- a locked voice bank, a full disk -- is
        # finished by running it again instead of refused.
        foreach ($child in @(Get-ChildItem -LiteralPath $MigrateFrom -Force)) {
            $target = Join-Path $DataRoot $child.Name
            if (Test-Path -LiteralPath $target) { continue }
            Move-Item -LiteralPath $child.FullName -Destination $target -ErrorAction Stop
        }
    } catch {
        # A resident panthera_host.exe holds voice banks open, and on the same
        # volume a move is a rename: it fails whole rather than half-moving.
        Write-Error ('Could not move the speech data: {0}' -f $_.Exception.Message)
        exit 4
    }
    # **Readable by everybody, writable by nobody but an administrator.**
    #
    # A same-volume move carries the source's security descriptor with it, so
    # data moved out of a profile arrives in ProgramData still readable by
    # that one account alone -- machine-wide in name and not in fact, and a
    # single-account machine cannot tell the difference, because SYSTEM reads
    # it either way.
    #
    # `icacls /reset` is the wrong repair.  What ProgramData grants by
    # inheritance is `BUILTIN\Users:(CI)(WD,AD,WEA,WA)`: every standard
    # account may create files and folders anywhere beneath it.  This tree is
    # not documents.  The host *maps and executes* the Mach-O inside it, and
    # NVDA reads this same root as SYSTEM on the sign-in screen -- so a folder
    # any user can write to is a local privilege escalation needing no unsafe
    # parsing at all: plant a generation folder, wait for the lock screen.
    #
    # Inheritance is cut and the three rights granted outright, by SID rather
    # than by name, because `BUILTIN\Users` is localised and this has to hold
    # on a Windows that does not speak English.
    #
    # Past this line the data has moved, so a failure here is a different
    # failure and has its own exit code: "it did not move" and "it moved but
    # the registry did not follow" need different things said to the person,
    # and telling them nothing changed when 1.6 GB just did is the one answer
    # that sends them looking in the wrong place.
    try {
        & "$env:SystemRoot\System32\icacls.exe" $DataRoot /inheritance:r `
            /grant '*S-1-5-18:(OI)(CI)F' `
            /grant '*S-1-5-32-544:(OI)(CI)F' `
            /grant '*S-1-5-32-545:(OI)(CI)RX' /T /C /Q | Out-Null
        Set-MachineDataPath $DataRoot
        Set-MachineSettings $MirrorSettings
        # Every registered token carries the old DataPath.  Follow the data,
        # all four lineages, the same way a deliberate folder change does.
        if (Test-AnyPantheraTokens) { Add-VoiceTokens @($GenerationTable.Folder) | Out-Null }
    } catch {
        Write-Error ('The data moved to {0}, but registering it there failed: {1}' -f $DataRoot,$_.Exception.Message)
        exit 5
    }
    if (!(@(Get-ChildItem -LiteralPath $MigrateFrom -Force)).Count) {
        Remove-Item -LiteralPath $MigrateFrom -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

if ($RegisterVoices) {
    & "$env:SystemRoot\SysWOW64\regsvr32.exe" /s (Join-Path $stage 'x86\panthera_sapi.dll')
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    & "$env:SystemRoot\System32\regsvr32.exe" /s (Join-Path $stage 'x64\panthera_sapi.dll')
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    # Registration is the elevated trip everybody makes, so it is where the
    # machine-wide copy of the settings gets refreshed.
    Set-MachineSettings $MirrorSettings
    Add-VoiceTokens $Generations | Out-Null
    exit 0
}
if ($UnregisterVoices) {
    Remove-VoiceTokens $Generations
    if (!(Test-AnyPantheraTokens)) {
        & "$env:SystemRoot\SysWOW64\regsvr32.exe" /s /u (Join-Path $stage 'x86\panthera_sapi.dll')
        & "$env:SystemRoot\System32\regsvr32.exe" /s /u (Join-Path $stage 'x64\panthera_sapi.dll')
    }
    exit 0
}

$form = New-Object Windows.Forms.Form
$form.Text = 'Panthera SAPI settings'; $form.Size = New-Object Drawing.Size(720,510)
$form.StartPosition = 'CenterScreen'; $form.AutoScaleMode = 'Dpi'
$label = New-Object Windows.Forms.Label
$label.Text = 'Mac OS X speech voices:'; $label.AutoSize = $true; $label.Location = New-Object Drawing.Point(12,14)
$list = New-Object Windows.Forms.CheckedListBox
$list.Name = 'speechEngineList'; $list.AccessibleName = 'Mac OS X speech engines'
$list.AccessibleDescription = 'Tiger, Leopard, and Lion speech data installation status'
$list.CheckOnClick = $true
$list.Location = New-Object Drawing.Point(12,38); $list.Size = New-Object Drawing.Size(680,200)
$status = New-Object Windows.Forms.Label
$status.Name = 'speechDataStatus'; $status.AccessibleName = 'Speech data status'
$status.Location = New-Object Drawing.Point(12,244); $status.Size = New-Object Drawing.Size(680,20)
# A real ProgressBar rather than text alone, because screen readers announce
# progress bar changes on their own -- NVDA's background beeps included --
# and a long extraction with a silent, frozen window is indistinguishable
# from a hang, which is exactly how it was reported.
$progress = New-Object Windows.Forms.ProgressBar
$progress.Name = 'extractionProgress'; $progress.AccessibleName = 'Extraction progress'
$progress.Location = New-Object Drawing.Point(12,266); $progress.Size = New-Object Drawing.Size(680,16)
$progress.Minimum = 0; $progress.Maximum = 100; $progress.Visible = $false

# The two NVDA driver settings SAPI users were living without.  Read by the
# engine DLL from HKCU on every utterance -- each Speak is a fresh host, so a
# change takes effect on the next thing spoken, in every SAPI application at
# once.  Commands default OFF for the same reason NVDA's checkbox does: the
# engine really parses [[...]], and measured, [[Main Page]] in a wiki article
# is not mispronounced but *eaten*.
$acceptCommands = New-Object Windows.Forms.CheckBox
$acceptCommands.Text = 'Accept e&mbedded speech commands in text'
$acceptCommands.AccessibleName = 'Accept embedded speech commands in text'
$acceptCommands.Location = New-Object Drawing.Point(12,290); $acceptCommands.AutoSize = $true
$pausesLabel = New-Object Windows.Forms.Label
$pausesLabel.Text = '&Pauses:'; $pausesLabel.AutoSize = $true
$pausesLabel.Location = New-Object Drawing.Point(420,294)
$pauses = New-Object Windows.Forms.ComboBox
$pauses.DropDownStyle = 'DropDownList'; $pauses.AccessibleName = 'Pauses'
# One setting, four generations, and it does not reach all of them: the phrase
# threshold is an engine tunable parameter, and tunable parameters arrived in
# MacinTalk 3.4.  Tiger's engine is 3.3, where measured, all five positions
# render byte-identical audio on every text tried.  It is not hidden, because
# this setting is global here rather than per generation and most people who
# have Tiger have a later one as well -- the Tiger driver in the NVDA add-on,
# which *is* per generation, does not offer it at all.
$pauses.AccessibleDescription =
  'Has no effect on Tiger voices: MacinTalk 3.3 has no tunable parameters.'
$pauses.Location = New-Object Drawing.Point(482,290); $pauses.Size = New-Object Drawing.Size(210,24)
$pausesValues = @('fewest','fewer','more','most','leopard')
foreach ($item in 'Fewest pauses','Fewer pauses','More pauses','Most pauses','Engine default') { [void]$pauses.Items.Add($item) }

# The rest of the NVDA driver's engine settings, ported.  Not ported, with
# reasons: sentence joining and the announcement gap are driver-architecture
# (SAPI applications control their own chunking), and the stress respelling
# assumes NVDA's symbol dictionary already turned ":" into the word "colon",
# which SAPI input never has.
$expandAbbrev = New-Object Windows.Forms.CheckBox
$expandAbbrev.Text = 'E&xpand abbreviations (Dr, kg)'
$expandAbbrev.AccessibleName = 'Expand abbreviations'
$expandAbbrev.Location = New-Object Drawing.Point(12,318); $expandAbbrev.AutoSize = $true
$rateBoost = New-Object Windows.Forms.CheckBox
$rateBoost.Text = 'Rate &boost'
$rateBoost.AccessibleName = 'Rate boost'
$rateBoost.Location = New-Object Drawing.Point(250,318); $rateBoost.AutoSize = $true
$inflLabel = New-Object Windows.Forms.Label
$inflLabel.Text = '&Inflection:'; $inflLabel.AutoSize = $true
$inflLabel.Location = New-Object Drawing.Point(360,320)
$inflection = New-Object Windows.Forms.NumericUpDown
$inflection.Minimum = 0; $inflection.Maximum = 100
$inflection.AccessibleName = 'Inflection'
$inflection.Location = New-Object Drawing.Point(432,318); $inflection.Size = New-Object Drawing.Size(56,24)
$numLabel = New-Object Windows.Forms.Label
$numLabel.Text = 'Long &numbers:'; $numLabel.AutoSize = $true
$numLabel.Location = New-Object Drawing.Point(500,320)
$numberStyle = New-Object Windows.Forms.ComboBox
$numberStyle.DropDownStyle = 'DropDownList'; $numberStyle.AccessibleName = 'Long numbers'
$numberStyle.Location = New-Object Drawing.Point(592,318); $numberStyle.Size = New-Object Drawing.Size(100,24)
$numberValues = @('fix','off')
foreach ($item in 'Fixed','Engine') { [void]$numberStyle.Items.Add($item) }

function Save-Setting([string]$name, $value) {
    New-Item -Path $dataPrefKey -Force | Out-Null
    Set-ItemProperty -Path $dataPrefKey -Name $name -Value $value
}
function Load-Setting([string]$name, $default) {
    try { (Get-ItemProperty -Path $dataPrefKey -Name $name -ErrorAction Stop).$name }
    catch { $default }
}
# This person's settings, packed for the elevated process to mirror into HKLM.
# Only the ones actually set travel: a value nobody chose has no business
# becoming the default every other account on the machine inherits.
function Get-SettingsArgument {
    $pairs = @()
    foreach ($name in $SettingNames) {
        $value = Load-Setting $name $null
        if ($null -ne $value) { $pairs += ('{0}={1}' -f $name,$value) }
    }
    $pairs -join ';'
}
$acceptCommands.Checked = [bool](Load-Setting 'AcceptCommands' 0)
$pauses.SelectedIndex = [Math]::Max(0, $pausesValues.IndexOf([string](Load-Setting 'Phrasing' 'fewest')))
$expandAbbrev.Checked = [bool](Load-Setting 'ExpandAbbreviations' 1)
$rateBoost.Checked = [bool](Load-Setting 'RateBoost' 0)
$inflection.Value = [Math]::Max(0, [Math]::Min(100, [int](Load-Setting 'Inflection' 50)))
$numberStyle.SelectedIndex = [Math]::Max(0, $numberValues.IndexOf([string](Load-Setting 'NumberStyle' 'fix')))
$diagnostics.Checked = [bool](Load-Setting 'Diagnostics' 0)
$acceptCommands.Add_CheckedChanged({ Save-Setting 'AcceptCommands' ([int]$acceptCommands.Checked) })
$pauses.Add_SelectedIndexChanged({ if ($pauses.SelectedIndex -ge 0) { Save-Setting 'Phrasing' $pausesValues[$pauses.SelectedIndex] } })
$expandAbbrev.Add_CheckedChanged({ Save-Setting 'ExpandAbbreviations' ([int]$expandAbbrev.Checked) })
$rateBoost.Add_CheckedChanged({ Save-Setting 'RateBoost' ([int]$rateBoost.Checked) })
$diagnostics.Add_CheckedChanged({ Save-Setting 'Diagnostics' ([int]$diagnostics.Checked) })
$inflection.Add_ValueChanged({ Save-Setting 'Inflection' ([int]$inflection.Value) })
$numberStyle.Add_SelectedIndexChanged({ if ($numberStyle.SelectedIndex -ge 0) { Save-Setting 'NumberStyle' $numberValues[$numberStyle.SelectedIndex] } })

function Refresh-Voices {
    $list.Items.Clear(); $voices = @(Get-Voices)
    foreach ($generation in $GenerationTable) {
        $count = @($voices | Where-Object Generation -eq $generation.Folder).Count
        $state = if ($count) { "$count voices" } else { 'not installed' }
        [void]$list.Items.Add(('{0} - {1}' -f $generation.Item,$state),$false)
    }
    if ($list.Items.Count) { $list.SelectedIndex = 0 }
    $status.Text = '{0} voice(s) found in {1}' -f $voices.Count,$data
}

# --- new data offers itself ----------------------------------------------
# The Register button being a manual step was confusing people: voice data
# extracted or pasted into the folder just sat there, silent, until somebody
# guessed.  On startup and after every extraction, any generation with data
# present and no token registered gets one yes/no offer -- and a "no" is
# remembered per generation, so declining once (or unregistering
# deliberately) is never nagged about.  Registering a generation clears its
# mark, whichever button or offer did it.
function Get-RegisteredGenerations {
    $found = @()
    foreach ($view in 'Registry32','Registry64') {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$view)
        $root = $base.OpenSubKey('Software\Microsoft\Speech\Voices\Tokens')
        if ($root) {
            foreach ($name in @($root.GetSubKeyNames())) {
                if ($name -like 'Panthera_*') { $found += ($name -split '_')[1] }
            }
            $root.Dispose()
        }
        $base.Dispose()
    }
    @($found | Sort-Object -Unique)
}
function Get-DeclinedGenerations {
    @(([string](Load-Setting 'DeclinedGenerations' '')) -split ',' | Where-Object { $_ })
}
function Set-DeclinedGenerations([string[]]$generations) {
    Save-Setting 'DeclinedGenerations' (@($generations | Sort-Object -Unique) -join ',')
}
function Offer-NewData {
    $present = @(Get-Voices | ForEach-Object Generation | Sort-Object -Unique)
    $registered = Get-RegisteredGenerations
    $declined = Get-DeclinedGenerations
    $pending = @($present | Where-Object { $_ -notin $registered -and $_ -notin $declined })
    if (!$pending.Count) { return }
    $labels = @($GenerationTable | Where-Object { $_.Folder -in $pending } | ForEach-Object Label) -join ', '
    $answer = [Windows.Forms.MessageBox]::Show($form,
        ("Voice data for {0} is installed but not registered with SAPI.`n`nRegister it now? (Choosing No will not ask again for these engines; the Register button always works.)" -f $labels),
        'Panthera SAPI','YesNo','Question')
    if ($answer -eq 'Yes') {
        $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1} -DataRoot "{2}" -MirrorSettings "{3}"' -f $settingsScript,($pending -join ','),$data,(Get-SettingsArgument)
        $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
        if ($process.ExitCode) {
            [Windows.Forms.MessageBox]::Show($form,'Registration failed. Use Register to try again.','Panthera SAPI','OK','Error') | Out-Null
        } else {
            Set-DeclinedGenerations @(Get-DeclinedGenerations | Where-Object { $_ -notin $pending })
            Refresh-Voices
        }
    } else {
        Set-DeclinedGenerations (@(Get-DeclinedGenerations) + $pending)
    }
}

# The one-time offer to move the data somewhere every account can read.
# Asked once and remembered, like the registration offer beside it: somebody
# who says no is not asked again, and the Choose data folder button has
# always been the way to move it by hand.
function Offer-Migration {
    if ([int](Load-Setting 'DeclinedMigration' 0)) { return }
    $plan = Get-MigrationPlan
    if ($plan.Action -ne 'migrate') { return }
    $answer = [Windows.Forms.MessageBox]::Show($form,
        ("Your MacinTalk speech data is in a folder only your Windows account can read:`n`n{0}`n`nMoving it to`n`n{1}`n`nlets every account on this machine use the voices, and leaves one copy instead of one per person. Nothing is re-extracted and the voices stay registered.`n`nMove it now? (This needs administrator permission.)" -f $plan.From,$plan.To),
        'Panthera SAPI','YesNo','Question')
    if ($answer -ne 'Yes') { Save-Setting 'DeclinedMigration' 1; return }
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -MigrateData -MigrateFrom "{1}" -DataRoot "{2}" -MirrorSettings "{3}"' -f $settingsScript,$plan.From,$plan.To,(Get-SettingsArgument)
    # **A cancelled elevation prompt is not a failure and not a success.**
    # `-Verb RunAs` writes a *non-terminating* error when somebody says no, so
    # without -ErrorAction Stop $process stays $null, `$null.ExitCode` is
    # neither 5 nor truthy, and the whole thing falls into the success branch:
    # HKCU repointed at an empty folder and a message saying the data now
    # lives there, with 1.6 GB still where it was.  Returning without
    # remembering a decline is right -- they did not decline, they backed out
    # of the prompt, and the offer should come round again.
    try {
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments -ErrorAction Stop
    } catch {
        return
    }
    if (!$process) { return }
    if ($process.ExitCode -eq 5) {
        # The folder moved and the registry did not follow, so the voices are
        # registered against a folder that is now empty.  Choose data folder
        # pointed at the new place re-registers them, which is the one action
        # that fixes it -- so say that, rather than "it failed".
        $script:data = $plan.To
        [Windows.Forms.MessageBox]::Show($form,
            ('The speech data was moved to {0}, but the voices could not be registered from there. Use Choose data folder, pick that folder, and the voices will be registered again.' -f $plan.To),
            'Panthera SAPI','OK','Warning') | Out-Null
        Refresh-Voices
        return
    }
    if ($process.ExitCode) {
        [Windows.Forms.MessageBox]::Show($form,
            ('The speech data could not be moved, and nothing was changed. Anything using a Panthera voice right now will be holding the files open -- close it and try again.'),
            'Panthera SAPI','OK','Error') | Out-Null
        return
    }
    $script:data = $plan.To
    # A remembered per-user folder now names a folder that is not there.  It
    # is only rewritten if it was set at all, so nobody acquires an explicit
    # choice they never made.
    try {
        $saved = (Get-ItemProperty -Path $dataPrefKey -Name DataPath -ErrorAction Stop).DataPath
        if ($saved) { Set-ItemProperty -Path $dataPrefKey -Name DataPath -Value $plan.To }
    } catch {}
    Refresh-Voices
    [Windows.Forms.MessageBox]::Show($form,
        ('The speech data now lives in {0}, where every account on this machine can read it.' -f $plan.To),
        'Panthera SAPI') | Out-Null
}

# **The voices point at a folder that is not there any more.**
#
# Each of the 96 tokens carries a DataPath written once, at registration.  The
# add-on searches for its data on every start; these do not.  So moving the
# folder by hand -- which is a reasonable thing to do, and which the add-on
# now follows -- leaves every token naming the old place, and the failure is
# the quietest one this project has: SAPI finds the voices, lists them, hands
# text to the engine, and the engine renders nothing.  Measured on Tomi's Rog
# from the sign-in screen: 24 utterances, every one returning its bookmark in
# 21-23 ms flat, no matter how long the words were.  Working voices on the
# same screen took 216 to 2164 ms.  A constant is not slow rendering, it is
# no rendering, and nothing in any log said so.
#
# Only offered when the remembered folder is genuinely gone and a real one has
# been found instead, so nobody is asked about a second copy they keep on
# purpose.
function Offer-Rebind {
    if (!(Test-AnyPantheraTokens)) { return }
    $registered = Get-TokenDataPath
    if (!$registered) { return }
    if (Test-SamePath $registered $data) { return }
    if (Test-Path -LiteralPath $registered) { return }
    if (!(@(Get-Voices).Count)) { return }
    $answer = [Windows.Forms.MessageBox]::Show($form,
        ("Your voices are registered against a folder that is no longer there:`n`n{0}`n`nThe speech data is here instead:`n`n{1}`n`nRegister the voices from that folder? Until this is done they will appear in every program's voice list and say nothing at all." -f $registered,$data),
        'Panthera SAPI','YesNo','Warning')
    if ($answer -ne 'Yes') { return }
    $all = @($GenerationTable.Folder) -join ','
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1} -DataRoot "{2}" -MirrorSettings "{3}"' -f $settingsScript,$all,$data,(Get-SettingsArgument)
    try {
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments -ErrorAction Stop
    } catch {
        return
    }
    if (!$process -or $process.ExitCode) {
        [Windows.Forms.MessageBox]::Show($form,'The voices could not be registered from that folder. Use Register to try again.','Panthera SAPI','OK','Error') | Out-Null
        return
    }
    Refresh-Voices
    [Windows.Forms.MessageBox]::Show($form,
        ('The voices are registered from {0} and will speak again.' -f $data),
        'Panthera SAPI') | Out-Null
}

# --- extraction, off the UI thread --------------------------------------
# The extractor prints "NN% message" lines as it works; running it inline
# froze the window for the whole image and read as a hang.  It runs as a
# child process now, with a timer reading its output to drive the progress
# bar and the status label, and the window stays alive throughout.
$script:extractProc = $null
$script:extractLog = $null
$script:extractImage = $null
$extractTimer = New-Object Windows.Forms.Timer
$extractTimer.Interval = 250

function Read-ExtractTail {
    try {
        $fs = [System.IO.File]::Open($script:extractLog, 'Open', 'Read', 'ReadWrite')
        try { $text = (New-Object System.IO.StreamReader($fs)).ReadToEnd() }
        finally { $fs.Dispose() }
        $lines = @($text -split "`r?`n" | Where-Object { $_ })
        if ($lines.Count) { $lines[-1] } else { $null }
    } catch { $null }
}

function Set-Busy([bool]$busy) {
    foreach ($control in @($extract,$register,$unregister,$chooseRoot)) { $control.Enabled = -not $busy }
    $progress.Visible = $busy
    if (-not $busy) { $progress.Value = 0 }
}

function Start-Extraction([string]$image, [bool]$replace) {
    $script:extractImage = $image
    $script:extractLog = [System.IO.Path]::GetTempFileName()
    New-Item -ItemType Directory -Force $data | Out-Null
    # -u for unbuffered stdout, so a progress line exists the moment the
    # extractor prints it rather than when a block fills.
    $argstr = '-u "{0}" "{1}" "{2}"' -f (Join-Path $stage 'extract.py'), $image, $data
    if ($replace) { $argstr += ' --replace' }
    # The installer ships an embeddable Python next to this script, so
    # extraction owes nothing to what is or is not on PATH; PATH python
    # stays as the fallback for a development stage without the bundle.
    $bundled = Join-Path $stage 'python\python.exe'
    $interpreter = if (Test-Path -LiteralPath $bundled) { $bundled } else { 'python' }
    try {
        $script:extractProc = Start-Process $interpreter -ArgumentList $argstr -RedirectStandardOutput $script:extractLog -WindowStyle Hidden -PassThru
        # Touching Handle while the process lives is what lets ExitCode be
        # read after it dies; without this every extraction reports failure.
        $null = $script:extractProc.Handle
    } catch {
        [Windows.Forms.MessageBox]::Show($form,'Python was not found. Extraction needs Python on PATH.','Panthera SAPI','OK','Error') | Out-Null
        return
    }
    Set-Busy $true
    $status.Text = 'Extracting speech data...'
    $extractTimer.Start()
}

$extractTimer.Add_Tick({
    $line = Read-ExtractTail
    if ($line -match '^(\d+)% (.*)$') {
        $progress.Value = [Math]::Min(100, [int]$matches[1])
        $status.Text = 'Extracting: {0}% - {1}' -f $progress.Value, $matches[2]
    }
    if ($script:extractProc -and $script:extractProc.HasExited) {
        $extractTimer.Stop()
        $code = $script:extractProc.ExitCode
        $script:extractProc = $null
        Set-Busy $false
        if ($code -eq 0) {
            $status.Text = 'Extraction finished.'
            Refresh-Voices
            [Windows.Forms.MessageBox]::Show($form,'Extraction finished.','Panthera SAPI') | Out-Null
            Offer-NewData
        } elseif ($code -eq 3) {
            # The extractor refused an occupied folder; replacing is a
            # decision, and it is the person's.
            $target = (Read-ExtractTail) -replace '^EXISTS ',''
            $status.Text = 'That speech data is already installed.'
            $answer = [Windows.Forms.MessageBox]::Show($form,
                ("This image's speech data is already installed at:`n{0}`n`nReplace it? The existing folder will be removed first." -f $target),
                'Panthera SAPI','YesNo','Question')
            if ($answer -eq 'Yes') { Start-Extraction $script:extractImage $true }
        } else {
            $status.Text = 'Extraction failed.'
            [Windows.Forms.MessageBox]::Show($form,'Extraction failed.','Panthera SAPI','OK','Error') | Out-Null
        }
    }
})

$selectAll = New-Object Windows.Forms.Button; $selectAll.Text = '&Select all'; $selectAll.Location = New-Object Drawing.Point(12,360); $selectAll.AutoSize=$true
$deselectAll = New-Object Windows.Forms.Button; $deselectAll.Text = '&Deselect all'; $deselectAll.Location = New-Object Drawing.Point(110,360); $deselectAll.AutoSize=$true
$chooseRoot = New-Object Windows.Forms.Button; $chooseRoot.Text = 'Data &location...'; $chooseRoot.Location = New-Object Drawing.Point(220,360); $chooseRoot.AutoSize=$true
# Diagnostics: off, and off means no file is created at all.  A checkbox
# rather than a registry value because the people most likely to be asked for
# a log are the least likely to want to be talked through regedit.  It offers
# level 1 only -- the measurements, which are what actually settle bugs.
# Level 2 adds the spoken text and stays a deliberate registry edit, because a
# transcript of everything the machine says should take more than one click.
$diagnostics = New-Object Windows.Forms.CheckBox
$diagnostics.Text = 'Write a &diagnostic log'
$diagnostics.AccessibleName = 'Write a diagnostic log'
$diagnostics.AccessibleDescription = 'Off by default. Records what the engine did, not what was spoken, to a file in your temp folder. Turn it on only if a bug report asks for it.'
$diagnostics.Location = New-Object Drawing.Point(340,362); $diagnostics.AutoSize = $true
$open = New-Object Windows.Forms.Button; $open.Text = '&Open data folder'; $open.Location = New-Object Drawing.Point(12,405); $open.AutoSize=$true
$extract = New-Object Windows.Forms.Button; $extract.Text = '&Extract from disc image...'; $extract.Location = New-Object Drawing.Point(145,405); $extract.AutoSize=$true
$register = New-Object Windows.Forms.Button; $register.Text = '&Register'; $register.Location = New-Object Drawing.Point(290,405); $register.AutoSize=$true
$unregister = New-Object Windows.Forms.Button; $unregister.Text = '&Unregister'; $unregister.Location = New-Object Drawing.Point(390,405); $unregister.AutoSize=$true
$close = New-Object Windows.Forms.Button; $close.Text = '&Close'; $close.Location = New-Object Drawing.Point(505,405); $close.AutoSize=$true

$selectAll.Add_Click({ for($i=0;$i -lt $list.Items.Count;$i++){$list.SetItemChecked($i,$true)} })
$deselectAll.Add_Click({ for($i=0;$i -lt $list.Items.Count;$i++){$list.SetItemChecked($i,$false)} })

$chooseRoot.Add_Click({
    $browser = New-Object Windows.Forms.FolderBrowserDialog
    $browser.Description = 'Choose the folder that holds the MacinTalk speech data (the generation folders live inside it).'
    if (Test-Path -LiteralPath $data) { $browser.SelectedPath = $data }
    if ($browser.ShowDialog($form) -eq 'OK') {
        $script:data = $browser.SelectedPath
        # Remembered per user, so the choice survives the next launch; the
        # elevated register call gets it as an argument instead, because the
        # elevated HKCU may belong to a different account.
        New-Item -Path $dataPrefKey -Force | Out-Null
        Set-ItemProperty -Path $dataPrefKey -Name DataPath -Value $script:data
        Refresh-Voices
        if (Test-AnyPantheraTokens) {
            # Every registered voice token carries the old DataPath, so a
            # deliberate folder change would leave them all pointing at the
            # folder that was just left behind.  Follow the data: re-register
            # every lineage against the new root.  All four are passed on
            # purpose -- Add-VoiceTokens removes each selected generation's
            # old tokens before adding what it finds, so a lineage with no
            # data here comes off the list instead of lingering mute.
            # Guarded on tokens existing at all, so browsing folders never
            # costs anyone an elevation prompt they did not earn.
            $all = @($GenerationTable.Folder) -join ','
            $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1} -DataRoot "{2}" -MirrorSettings "{3}"' -f $settingsScript,$all,$script:data,(Get-SettingsArgument)
            $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
            if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'The folder was remembered, but re-registering the voices from it failed. Use Register to try again.','Panthera SAPI','OK','Error') }
            else { [Windows.Forms.MessageBox]::Show($form,'Voices are now registered from the new folder.','Panthera SAPI') }
            Refresh-Voices
        }
    }
})

$open.Add_Click({ New-Item -ItemType Directory -Force $data | Out-Null; Start-Process explorer.exe -ArgumentList ('"{0}"' -f $data) })
$extract.Add_Click({
    $picker = New-Object Windows.Forms.OpenFileDialog; $picker.Title='Choose a Mac OS X install disc image'; $picker.Filter='Disc images|*.iso;*.dmg;*.cdr|All files|*.*'
    if ($picker.ShowDialog($form) -eq 'OK') {
        Start-Extraction $picker.FileName $false
    }
})
$register.Add_Click({
    $selected=@(); for($i=0;$i -lt $GenerationTable.Count;$i++){if($list.GetItemChecked($i)){$selected+=$GenerationTable[$i].Folder}}
    if(!$selected.Count){[Windows.Forms.MessageBox]::Show($form,'Check at least one engine.','Panthera SAPI');return}
    $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1} -DataRoot "{2}" -MirrorSettings "{3}"' -f $settingsScript,($selected -join ','),$data,(Get-SettingsArgument)
    $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'Registration failed.','Panthera SAPI','OK','Error') } else {
        # A deliberate registration lifts the never-ask-again mark.
        Set-DeclinedGenerations @(Get-DeclinedGenerations | Where-Object { $_ -notin $selected })
        [Windows.Forms.MessageBox]::Show($form,'Panthera voices were registered for 32-bit and 64-bit SAPI.','Panthera SAPI')
    }
})
$unregister.Add_Click({
    $selected=@(); for($i=0;$i -lt $GenerationTable.Count;$i++){if($list.GetItemChecked($i)){$selected+=$GenerationTable[$i].Folder}}
    if(!$selected.Count){[Windows.Forms.MessageBox]::Show($form,'Check at least one engine.','Panthera SAPI');return}
    $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -UnregisterVoices -GenerationList {1} -DataRoot "{2}"' -f $settingsScript,($selected -join ','),$data
    $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'Unregistration failed.','Panthera SAPI','OK','Error') } else {
        # Unregistering is the person saying no: mark it, so the startup
        # offer never asks to re-register what was just removed.
        Set-DeclinedGenerations (@(Get-DeclinedGenerations) + $selected)
        [Windows.Forms.MessageBox]::Show($form,'Panthera voices were unregistered.','Panthera SAPI')
    }
})
$close.Add_Click({$form.Close()})
$form.AcceptButton=$register; $form.CancelButton=$close
$form.Controls.AddRange(@($label,$list,$status,$progress,$acceptCommands,$pausesLabel,$pauses,$expandAbbrev,$rateBoost,$inflLabel,$inflection,$numLabel,$numberStyle,$selectAll,$deselectAll,$chooseRoot,$diagnostics,$open,$extract,$register,$unregister,$close))
$form.Add_FormClosing({
    if ($script:extractProc -and -not $script:extractProc.HasExited) {
        try { $script:extractProc.Kill() } catch {}
    }
})
# Migration first, registration second: moving the data re-registers the
# tokens against the new root on its way out, so asking about registration
# before the move would ask about a folder that is about to be left behind.
Refresh-Voices; $form.Add_Shown({$list.Focus(); Offer-Rebind; Offer-Migration; Offer-NewData}); [void]$form.ShowDialog()
