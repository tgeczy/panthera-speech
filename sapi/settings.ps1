param([switch]$RegisterVoices,[switch]$UnregisterVoices,[string]$GenerationList)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$stage = Split-Path -Parent $MyInvocation.MyCommand.Path
$settingsScript = $PSCommandPath
$data = Join-Path $env:APPDATA 'Panthera SAPI'
$clsid = '{C1F7FC55-3512-4F5D-A6EB-F53220BE4693}'
$Generations = @($GenerationList -split ',' | Where-Object { $_ })

New-Item -ItemType Directory -Force $data | Out-Null
foreach ($generationFolder in 'Tiger','Leopard','Lion') {
    New-Item -ItemType Directory -Force (Join-Path $data $generationFolder) | Out-Null
}

function Get-Voices {
    $rows = @()
    foreach ($generation in @(@('Tiger','Tiger'),@('Leopard','Leopard'),@('Lion','Lion'))) {
        $folder = Join-Path $data "$($generation[0])\Speech\Voices"
        if (Test-Path -LiteralPath $folder) {
            Get-ChildItem -LiteralPath $folder -Directory -Filter '*.SpeechVoice' | Sort-Object Name | ForEach-Object {
                $voiceName = $_.Name.Substring(0, $_.Name.Length - '.SpeechVoice'.Length)
                $rows += [pscustomobject]@{ Generation=$generation[0]; Label=$generation[1]; Voice=$voiceName }
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
$list.Location = New-Object Drawing.Point(12,38); $list.Size = New-Object Drawing.Size(680,270)
$status = New-Object Windows.Forms.Label
$status.Name = 'speechDataStatus'; $status.AccessibleName = 'Speech data status'
$status.Location = New-Object Drawing.Point(12,316); $status.Size = New-Object Drawing.Size(680,38)

function Refresh-Voices {
    $list.Items.Clear(); $voices = @(Get-Voices)
    foreach ($generation in @(@('Tiger','Tiger - Mac OS X 10.4'),@('Leopard','Leopard - Mac OS X 10.5'),@('Lion','Lion - Mac OS X 10.7'))) {
        $count = @($voices | Where-Object Generation -eq $generation[0]).Count
        $state = if ($count) { "$count voices" } else { 'not installed' }
        [void]$list.Items.Add(('{0} - {1}' -f $generation[1],$state),$false)
    }
    if ($list.Items.Count) { $list.SelectedIndex = 0 }
    $status.Text = '{0} voice(s) found in {1}' -f $voices.Count,$data
}

$selectAll = New-Object Windows.Forms.Button; $selectAll.Text = '&Select all'; $selectAll.Location = New-Object Drawing.Point(12,360); $selectAll.AutoSize=$true
$deselectAll = New-Object Windows.Forms.Button; $deselectAll.Text = '&Deselect all'; $deselectAll.Location = New-Object Drawing.Point(110,360); $deselectAll.AutoSize=$true
$open = New-Object Windows.Forms.Button; $open.Text = '&Open data folder'; $open.Location = New-Object Drawing.Point(12,405); $open.AutoSize=$true
$extract = New-Object Windows.Forms.Button; $extract.Text = '&Extract from ISO...'; $extract.Location = New-Object Drawing.Point(145,405); $extract.AutoSize=$true
$register = New-Object Windows.Forms.Button; $register.Text = '&Register'; $register.Location = New-Object Drawing.Point(290,405); $register.AutoSize=$true
$unregister = New-Object Windows.Forms.Button; $unregister.Text = '&Unregister'; $unregister.Location = New-Object Drawing.Point(390,405); $unregister.AutoSize=$true
$close = New-Object Windows.Forms.Button; $close.Text = '&Close'; $close.Location = New-Object Drawing.Point(505,405); $close.AutoSize=$true

$selectAll.Add_Click({ for($i=0;$i -lt $list.Items.Count;$i++){$list.SetItemChecked($i,$true)} })
$deselectAll.Add_Click({ for($i=0;$i -lt $list.Items.Count;$i++){$list.SetItemChecked($i,$false)} })

$open.Add_Click({ New-Item -ItemType Directory -Force $data | Out-Null; Start-Process explorer.exe -ArgumentList ('"{0}"' -f $data) })
$extract.Add_Click({
    $picker = New-Object Windows.Forms.OpenFileDialog; $picker.Title='Choose a Mac OS X install disc image'; $picker.Filter='Disc images|*.iso;*.dmg;*.cdr|All files|*.*'
    if ($picker.ShowDialog($form) -eq 'OK') {
        $status.Text='Extracting speech data. Please wait.'; $form.Refresh()
        & python (Join-Path $stage 'extract.py') $picker.FileName $data
        if ($LASTEXITCODE) { [Windows.Forms.MessageBox]::Show($form,'Extraction failed.','Panthera SAPI','OK','Error') } else { [Windows.Forms.MessageBox]::Show($form,'Extraction finished.','Panthera SAPI') }
        Refresh-Voices
    }
})
$register.Add_Click({
    $selected=@(); for($i=0;$i -lt 3;$i++){if($list.GetItemChecked($i)){$selected+=@('Tiger','Leopard','Lion')[$i]}}
    if(!$selected.Count){[Windows.Forms.MessageBox]::Show($form,'Check at least one engine.','Panthera SAPI');return}
    $arguments='-NoProfile -ExecutionPolicy Bypass -STA -File "{0}" -RegisterVoices -GenerationList {1}' -f $settingsScript,($selected -join ',')
    $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'Registration failed.','Panthera SAPI','OK','Error') } else { [Windows.Forms.MessageBox]::Show($form,'Panthera voices were registered for 32-bit and 64-bit SAPI.','Panthera SAPI') }
})
$unregister.Add_Click({
    $selected=@(); for($i=0;$i -lt 3;$i++){if($list.GetItemChecked($i)){$selected+=@('Tiger','Leopard','Lion')[$i]}}
    if(!$selected.Count){[Windows.Forms.MessageBox]::Show($form,'Check at least one engine.','Panthera SAPI');return}
    $arguments='-NoProfile -ExecutionPolicy Bypass -STA -File "{0}" -UnregisterVoices -GenerationList {1}' -f $settingsScript,($selected -join ',')
    $process=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    if ($process.ExitCode) { [Windows.Forms.MessageBox]::Show($form,'Unregistration failed.','Panthera SAPI','OK','Error') } else { [Windows.Forms.MessageBox]::Show($form,'Panthera voices were unregistered.','Panthera SAPI') }
})
$close.Add_Click({$form.Close()})
$form.AcceptButton=$register; $form.CancelButton=$close
$form.Controls.AddRange(@($label,$list,$status,$selectAll,$deselectAll,$open,$extract,$register,$unregister,$close))
Refresh-Voices; $form.Add_Shown({$list.Focus()}); [void]$form.ShowDialog()
