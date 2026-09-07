# Exercise the real WinForms controls without displaying the dialog, offering
# registration or touching personal settings. Both hives are isolated in HKCU.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$source = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'settings.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0] }
$names = $ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    $_.Left.Extent.Text -eq '$SettingNames'
}
. ([scriptblock]::Create($names.Extent.Text))
$start = $source.IndexOf('$form = New-Object Windows.Forms.Form')
$end = $source.IndexOf('Refresh-Voices; $form.Add_Shown')
if ($start -lt 0 -or $end -le $start) { throw 'Settings UI entry points missing' }
$ui = [scriptblock]::Create($source.Substring($start,$end-$start))
$testPath = 'HKCU:\Software\PantheraTests\Settings-' + [guid]::NewGuid().ToString('N')
$dataPrefKey = $testPath + '\User'
$machinePrefPath = 'Software\Panthera SAPI'
$machineTestKey = $testPath + '\Machine'
# Redirect only reads of the production machine settings key.
function Get-Item([string]$LiteralPath) {
    if ($LiteralPath -eq ('HKLM:\' + $machinePrefPath)) { $LiteralPath = $machineTestKey }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -ErrorAction Stop
}
function Check($ok,[string]$message) { if (!$ok) { throw $message }; Write-Output "ok: $message" }
try {
    New-Item -Path $dataPrefKey,$machineTestKey -Force | Out-Null
    New-ItemProperty $machineTestKey ExpandAbbreviations -Value 0 -PropertyType DWord | Out-Null
    New-ItemProperty $machineTestKey Phrasing -Value 'more' -PropertyType String | Out-Null
    New-ItemProperty $machineTestKey Diagnostics -Value 1 -PropertyType DWord | Out-Null
    New-ItemProperty $dataPrefKey RateBoost -Value 1 -PropertyType DWord | Out-Null
    . $ui
    Check (!$expandAbbrev.Checked) 'UI shows inherited abbreviation setting'
    Check ($pauses.SelectedIndex -eq 2) 'UI shows inherited phrase setting'
    Check ($diagnostics.Checked) 'diagnostics exists before loading and binding'
    Check ((Get-SettingsArgument) -eq 'RateBoost=1') 'opening UI does not turn inherited settings into user overrides'
    $expandAbbrev.Checked = $true
    $diagnostics.Checked = $false
    $pauses.SelectedIndex = 3
    Check ((Load-Setting ExpandAbbreviations 0) -eq 1) 'checkbox saves immediately'
    Check ((Load-Setting Diagnostics 1) -eq 0) 'diagnostics checkbox is connected'
    Check ((Load-Setting Phrasing '') -eq 'most') 'phrase list saves immediately'
    foreach ($control in $expandAbbrev,$acceptCommands,$rateBoost,$inflection,$numberStyle,$pauses) {
        Check ($control.Parent -eq $form -and $control.AccessibleName.Length -gt 0) 'native setting control has a parent and an accessible name'
    }
    $form.Dispose(); $extractTimer.Dispose()
    . $ui
    Check ($expandAbbrev.Checked -and !$diagnostics.Checked -and $pauses.SelectedIndex -eq 3) 'recreated UI restores user choices over machine defaults'
    # The engine ignores values of the wrong registry type; the UI must too.
    New-ItemProperty $dataPrefKey ExpandAbbreviations -Value 'wrong type' -PropertyType String -Force | Out-Null
    Check ((Load-EngineSetting ExpandAbbreviations 1) -eq 0) 'wrong-type user value falls back to the machine'
    $expandAbbrev.Checked = $false
    $expandAbbrev.Checked = $true
    Check ((Load-EngineSetting ExpandAbbreviations 0) -eq 1) 'changing the checkbox repairs a wrong-type preference'
    Check ((Load-EngineSetting Inflection 50) -eq 50) 'missing settings use engine defaults'
} finally {
    if ($form) { $form.Dispose() }
    if ($extractTimer) { $extractTimer.Dispose() }
    # Only the unique test key created above; never the real preference key.
    Remove-Item -LiteralPath $testPath -Recurse -Force
}
