param([switch]$RegisterVoices,[switch]$UnregisterVoices,[string]$GenerationList,[string]$DataRoot)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$stage = Split-Path -Parent $MyInvocation.MyCommand.Path
$settingsScript = $PSCommandPath
$clsid = '{C1F7FC55-3512-4F5D-A6EB-F53220BE4693}'
$Generations = @($GenerationList -split ',' | Where-Object { $_ })

# Where the MacinTalk data lives.  Three answers, in order: the folder the
# user chose (remembered in HKCU, and passed explicitly through the elevated
# re-invocation, whose HKCU may not be this user's); NVDA's own shared
# macintalk folder, so an NVDA user registers SAPI voices from the data they
# already extracted rather than extracting everything twice; and a standalone
# default for a machine with no NVDA at all.
$dataPrefKey = 'HKCU:\Software\Panthera SAPI'
function Resolve-DataRoot {
    if ($DataRoot) { return $DataRoot }
    try {
        $saved = (Get-ItemProperty -Path $dataPrefKey -Name DataPath -ErrorAction Stop).DataPath
        if ($saved -and (Test-Path -LiteralPath $saved)) { return $saved }
    } catch {}
    $nvda = Join-Path $env:APPDATA 'nvda\macintalk'
    if (Test-Path -LiteralPath $nvda) { return $nvda }
    Join-Path $env:APPDATA 'macintalk-data'
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
            $key = $root.CreateSubKey($name)
            $key.SetValue('',('{0} ({1})' -f $voice.Voice,$voice.Label))
            $key.SetValue('CLSID',$clsid); $key.SetValue('VoiceName',$voice.Voice)
            $key.SetValue('Generation',$voice.Generation); $key.SetValue('DataPath',$data)
            $attributes = $key.CreateSubKey('Attributes')
            $attributes.SetValue('Name',$voice.Voice); $attributes.SetValue('Vendor','Panthera Speech')
            $attributes.SetValue('Language','409'); $attributes.SetValue('Gender','Neutral')
            $attributes.Dispose(); $key.Dispose()
        }
        $root.Dispose(); $base.Dispose()
    }
    $voices.Count
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

if ($RegisterVoices) {
    & "$env:SystemRoot\SysWOW64\regsvr32.exe" /s (Join-Path $stage 'x86\panthera_sapi.dll')
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    & "$env:SystemRoot\System32\regsvr32.exe" /s (Join-Path $stage 'x64\panthera_sapi.dll')
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
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
$pauses.Location = New-Object Drawing.Point(482,290); $pauses.Size = New-Object Drawing.Size(210,24)
$pausesValues = @('fewest','fewer','more','most','leopard')
foreach ($item in 'Fewest pauses','Fewer pauses','More pauses','Most pauses','Engine default') { [void]$pauses.Items.Add($item) }

# The rest of the NVDA driver's engine settings, ported.  Not ported, with
# reasons: sentence joining and the announcement gap are driver-architecture
# (SAPI applications control their own chunking, and each utterance is its
# own process), and the stress respelling assumes NVDA's symbol dictionary
# already turned ":" into the word "colon", which SAPI input never has.
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
$acceptCommands.Checked = [bool](Load-Setting 'AcceptCommands' 0)
$pauses.SelectedIndex = [Math]::Max(0, $pausesValues.IndexOf([string](Load-Setting 'Phrasing' 'fewest')))
$expandAbbrev.Checked = [bool](Load-Setting 'ExpandAbbreviations' 1)
$rateBoost.Checked = [bool](Load-Setting 'RateBoost' 0)
$inflection.Value = [Math]::Max(0, [Math]::Min(100, [int](Load-Setting 'Inflection' 50)))
$numberStyle.SelectedIndex = [Math]::Max(0, $numberValues.IndexOf([string](Load-Setting 'NumberStyle' 'fix')))
$acceptCommands.Add_CheckedChanged({ Save-Setting 'AcceptCommands' ([int]$acceptCommands.Checked) })
$pauses.Add_SelectedIndexChanged({ if ($pauses.SelectedIndex -ge 0) { Save-Setting 'Phrasing' $pausesValues[$pauses.SelectedIndex] } })
$expandAbbrev.Add_CheckedChanged({ Save-Setting 'ExpandAbbreviations' ([int]$expandAbbrev.Checked) })
$rateBoost.Add_CheckedChanged({ Save-Setting 'RateBoost' ([int]$rateBoost.Checked) })
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
    try {
        $script:extractProc = Start-Process python -ArgumentList $argstr -RedirectStandardOutput $script:extractLog -WindowStyle Hidden -PassThru
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
$open = New-Object Windows.Forms.Button; $open.Text = '&Open data folder'; $open.Location = New-Object Drawing.Point(12,405); $open.AutoSize=$true
$extract = New-Object Windows.Forms.Button; $extract.Text = '&Extract from ISO...'; $extract.Location = New-Object Drawing.Point(145,405); $extract.AutoSize=$true
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
            $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1} -DataRoot "{2}"' -f $settingsScript,$all,$script:data
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
    $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1} -DataRoot "{2}"' -f $settingsScript,($selected -join ','),$data
    $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'Registration failed.','Panthera SAPI','OK','Error') } else { [Windows.Forms.MessageBox]::Show($form,'Panthera voices were registered for 32-bit and 64-bit SAPI.','Panthera SAPI') }
})
$unregister.Add_Click({
    $selected=@(); for($i=0;$i -lt $GenerationTable.Count;$i++){if($list.GetItemChecked($i)){$selected+=$GenerationTable[$i].Folder}}
    if(!$selected.Count){[Windows.Forms.MessageBox]::Show($form,'Check at least one engine.','Panthera SAPI');return}
    $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "{0}" -UnregisterVoices -GenerationList {1} -DataRoot "{2}"' -f $settingsScript,($selected -join ','),$data
    $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'Unregistration failed.','Panthera SAPI','OK','Error') } else { [Windows.Forms.MessageBox]::Show($form,'Panthera voices were unregistered.','Panthera SAPI') }
})
$close.Add_Click({$form.Close()})
$form.AcceptButton=$register; $form.CancelButton=$close
$form.Controls.AddRange(@($label,$list,$status,$progress,$acceptCommands,$pausesLabel,$pauses,$expandAbbrev,$rateBoost,$inflLabel,$inflection,$numLabel,$numberStyle,$selectAll,$deselectAll,$chooseRoot,$open,$extract,$register,$unregister,$close))
$form.Add_FormClosing({
    if ($script:extractProc -and -not $script:extractProc.HasExited) {
        try { $script:extractProc.Kill() } catch {}
    }
})
Refresh-Voices; $form.Add_Shown({$list.Focus()}); [void]$form.ShowDialog()
