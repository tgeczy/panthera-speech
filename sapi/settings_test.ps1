# Exercise the real WinForms controls without displaying the dialog, offering
# registration or touching personal settings.  The two settings files are
# scratch files under %TEMP%; both registry hives are isolated in HKCU.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$source = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'settings.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($assignment in ($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    $_.Left.Extent.Text -in '$SettingKinds','$SettingNames'
})) { . ([scriptblock]::Create($assignment.Extent.Text)) }
if (!$SettingNames) { throw 'Setting names missing' }
# The file reader and writer and the elevated mirror stand above the form so
# the elevated trips can call them; pulled in by name.
$helpers = 'Read-SettingsFile','Write-SettingsFile','Set-TableValue','Set-SettingsFileValue',
           'Get-SettingsFileValue','Set-MachineSettings'
foreach ($fn in $ast.FindAll({ $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] },$false)) {
    if ($fn.Name -in $helpers) { . ([scriptblock]::Create($fn.Extent.Text)) }
}
$start = $source.IndexOf('$form = New-Object Windows.Forms.Form')
$end = $source.IndexOf('Refresh-Voices; $form.Add_Shown')
if ($start -lt 0 -or $end -le $start) { throw 'Settings UI entry points missing' }
$ui = [scriptblock]::Create($source.Substring($start,$end-$start))
$testId = [guid]::NewGuid().ToString('N')
$testPath = 'HKCU:\Software\PantheraTests\Settings-' + $testId
$dataPrefKey = $testPath + '\User'
# The "machine" hive is this same test key, so the elevated path can be run
# for real -- seeding, writing, and emptying -- without going near HKLM.
$machineHive = 'CurrentUser'
$machinePrefPath = 'Software\PantheraTests\Settings-' + $testId + '\Machine'
$machineTestKey = 'HKCU:\' + $machinePrefPath
$testDir = Join-Path $env:TEMP ('panthera-settings-test-' + $testId)
$userSettingsFile = Join-Path $testDir 'user\settings.toml'
$machineSettingsFile = Join-Path $testDir 'machine\settings.toml'
# Redirect only reads of the production machine settings key.
function Get-Item([string]$LiteralPath) {
    if ($LiteralPath -eq ('HKLM:\' + $machinePrefPath)) { $LiteralPath = $machineTestKey }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -ErrorAction Stop
}
function Check($ok,[string]$message) { if (!$ok) { throw $message }; Write-Output "ok: $message" }
function Lines([string]$path) { @(Get-Content -LiteralPath $path -Encoding UTF8) }
try {
    New-Item -Path $dataPrefKey,$machineTestKey -Force | Out-Null
    New-Item -ItemType Directory -Path $testDir -Force | Out-Null

    # The file format on its own: escapes, a negative, a number too big for
    # a DWORD, a trailing comment, a repeated key, and lines that do not
    # parse -- the DLL's reader has the same cases in settings_test.cpp.
    $probe = Join-Path $testDir 'probe.toml'
    Set-Content -LiteralPath $probe -Encoding UTF8 -Value @(
        '# a comment', 'Phrasing = "a \"quoted\" \\ word"', 'Inflection = -5',
        'RateBoost = 99999999999', 'Diagnostics = 2 # trailing comment',
        'Phrasing = "later"', 'Weird key = 1', '= 3', 'AcceptCommands = true',
        'Open = "never closed', ('NumberStyle = "' + ('x' * 70) + '"'))
    $table = Read-SettingsFile $probe
    Check (@($table).Count -eq 6) 'lines that do not parse are left out and nothing else is'
    Check ($table[0].Value -eq 'a "quoted" \ word' -and $table[0].Value -is [string]) 'a quoted word reads with its escapes'
    Check ($table[1].Value -eq -5 -and $table[1].Value -is [int]) 'a number reads as a number'
    Check ($table[2].Value -eq 2) 'a trailing comment is not part of the number'
    Check ($table[4].Value -eq 1 -and $table[4].Value -is [int]) 'true reads as 1'
    Check ($table[5].Value.Length -eq 63) 'a string is capped where the registry buffer capped it'
    Check ((Get-SettingsFileValue $probe Phrasing 'String') -eq 'later') 'a repeated key means the last line'
    Check ($null -eq (Get-SettingsFileValue $probe Inflection 'String')) 'a typed lookup passes over the other type'
    $again = Join-Path $testDir 'again.toml'
    Check (Write-SettingsFile $again $table) 'a file can be written'
    Check ((Lines $again) -contains 'Phrasing = "a \"quoted\" \\ word"' -and (Lines $again) -contains 'AcceptCommands = 1') 'writing quotes words, escapes them, and writes numbers as numbers'
    $back = Read-SettingsFile $again
    Check (@($back).Count -eq 6 -and $back[0].Value -eq $table[0].Value -and $back[5].Value -eq $table[5].Value) 'a written file reads back as it was'

    # What a machine upgraded from the registry build looks like: HKLM
    # mirrored months ago, HKCU holding one choice, tool state and a value of
    # the wrong type, and no file anywhere.
    New-ItemProperty $machineTestKey ExpandAbbreviations -Value 0 -PropertyType DWord | Out-Null
    New-ItemProperty $machineTestKey Phrasing -Value 'more' -PropertyType String | Out-Null
    New-ItemProperty $machineTestKey Diagnostics -Value 1 -PropertyType DWord | Out-Null
    New-ItemProperty $machineTestKey Inflection -Value 30 -PropertyType DWord | Out-Null
    New-ItemProperty $machineTestKey NumberStyle -Value 'words' -PropertyType String | Out-Null
    New-ItemProperty $dataPrefKey RateBoost -Value 1 -PropertyType DWord | Out-Null
    New-ItemProperty $dataPrefKey DataPath -Value 'C:\somewhere' -PropertyType String | Out-Null
    New-ItemProperty $dataPrefKey NumberStyle -Value 7 -PropertyType DWord | Out-Null
    . $ui
    Check (!$expandAbbrev.Checked) 'UI shows inherited abbreviation setting'
    Check ($pauses.SelectedIndex -eq 2) 'UI shows inherited phrase setting'
    Check ($diagnostics.Checked) 'diagnostics exists before loading and binding'
    Check ($inflection.Value -eq 30) 'UI shows inherited inflection'
    Check ((Get-SettingsArgument) -eq 'RateBoost=1') 'opening UI does not turn inherited settings into user overrides'
    Check ((Lines $userSettingsFile) -contains 'RateBoost = 1') 'opening the UI moves the registry choice into the user file as a number'
    $left = Get-ItemProperty -Path $dataPrefKey
    Check ($null -eq $left.RateBoost) 'the moved value leaves the registry'
    Check ($left.DataPath -eq 'C:\somewhere' -and $left.NumberStyle -eq 7) 'tool state and a wrong-typed value stay where they were'
    Check (!(Test-Path -LiteralPath $machineSettingsFile)) 'opening the UI writes no machine file'
    $expandAbbrev.Checked = $true
    $diagnostics.Checked = $false
    $pauses.SelectedIndex = 3
    Check ((Load-Setting ExpandAbbreviations 0) -eq 1) 'checkbox saves immediately'
    Check ((Load-Setting Diagnostics 1) -eq 0) 'diagnostics checkbox is connected'
    Check ((Load-Setting Phrasing '') -eq 'most') 'phrase list saves immediately'
    Check ((Lines $userSettingsFile) -contains 'Phrasing = "most"' -and (Lines $userSettingsFile) -contains 'ExpandAbbreviations = 1') 'a word is written in quotes and a checkbox as a number'
    Check ((Get-SettingsFileValue $machineSettingsFile ExpandAbbreviations 'DWord') -eq 1 -and (Get-SettingsFileValue $machineSettingsFile Phrasing 'String') -eq 'most') 'a save reaches the machine file too'
    Check ($null -eq (Get-SettingsFileValue $machineSettingsFile RateBoost $null)) 'a save carries only the setting that changed'
    foreach ($control in $expandAbbrev,$acceptCommands,$rateBoost,$inflection,$numberStyle,$pauses) {
        Check ($control.Parent -eq $form -and $control.AccessibleName.Length -gt 0) 'native setting control has a parent and an accessible name'
    }
    $form.Dispose(); $extractTimer.Dispose()
    . $ui
    Check ($expandAbbrev.Checked -and !$diagnostics.Checked -and $pauses.SelectedIndex -eq 3) 'recreated UI restores user choices over machine defaults'
    Check ((Get-SettingsArgument) -eq 'Phrasing=most;ExpandAbbreviations=1;RateBoost=1;Diagnostics=0') 'the argument carries every explicit choice and nothing inherited'
    # The engine passes over a value of the wrong type; the UI must too.  A
    # hand edit: a word where a number belongs, and a line that does not parse.
    Add-Content -LiteralPath $userSettingsFile -Encoding UTF8 -Value 'Inflection = "loud"','torn line','AcceptCommands = true'
    Check ((Load-EngineSetting Inflection 50) -eq 30) 'wrong-type file value falls back to the next source'
    Check ((Load-EngineSetting AcceptCommands 0) -eq 1) 'a hand-written true reads as on'
    $inflection.Value = 40
    Check ((Load-EngineSetting Inflection 50) -eq 40) 'changing the control repairs a wrong-typed line'
    Check (@(Lines $userSettingsFile | Where-Object { $_ -like 'Inflection*' }).Count -eq 1) 'the repaired line replaces the broken one in place'
    Check ((Load-EngineSetting NumberStyle 'fix') -eq 'words') 'a setting nobody chose here still comes from the machine registry'
    Check ((Load-EngineSetting ReadTimeoutMs 30000) -eq 30000) 'missing settings use engine defaults'

    # The elevated trip: this person's choices into the machine file, what
    # HKLM still held seeded in first, HKLM emptied of the engine settings,
    # and a name that is not a setting ignored.
    Set-MachineSettings ((Get-SettingsArgument) + ';Bogus=3;RateBoost=0')
    Check ((Get-SettingsFileValue $machineSettingsFile Inflection 'DWord') -eq 40 -and (Get-SettingsFileValue $machineSettingsFile RateBoost 'DWord') -eq 0) 'the elevated trip writes the choices it was handed'
    Check ((Lines $machineSettingsFile) -contains 'NumberStyle = "words"') 'what HKLM still held is seeded into the machine file, quoted'
    Check ($null -eq (Get-SettingsFileValue $machineSettingsFile Bogus $null)) 'a name that is not a setting is not written'
    $machineLeft = Get-ItemProperty -Path $machineTestKey
    Check ($null -eq $machineLeft.NumberStyle -and $null -eq $machineLeft.Inflection) 'the elevated trip empties HKLM of the engine settings'
    Check ((Load-EngineSetting NumberStyle 'fix') -eq 'words') 'the seeded value is what the UI now inherits'
} finally {
    if ($form) { $form.Dispose() }
    if ($extractTimer) { $extractTimer.Dispose() }
    # Only the unique test key and folder created above; never the real ones.
    Remove-Item -LiteralPath $testPath -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testDir -Recurse -Force -ErrorAction SilentlyContinue
}
