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
    $_.Left.Extent.Text -in '$SettingKinds','$SettingNames','$GenerationTable'
})) { . ([scriptblock]::Create($assignment.Extent.Text)) }
if (!$SettingNames) { throw 'Setting names missing' }
# The file reader and writer and the elevated mirror stand above the form so
# the elevated trips can call them; pulled in by name.
$helpers = 'Read-SettingsFile','Write-SettingsFile','Set-TableValue','Set-SettingsFileValue',
           'Get-SettingsFileValue','Set-MachineSettings',
           'ConvertTo-Argument','Format-ElevatedArguments','Invoke-Elevated','Get-ComServers','Invoke-Regsvr32','Test-Arm64'
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

    # The elevated command line (panthera-speech#35).  A drive root is what a
    # folder picker returns for E:, and "E:\" escaped its own closing quote:
    # the elevated script got DataRoot = 'E:" -MirrorSettings ...' and no
    # settings.  Run the real template against a stub with the same names.
    $stub = Join-Path $testDir 'stub.ps1'
    $heard = Join-Path $testDir 'heard.txt'
    Set-Content -LiteralPath $stub -Encoding UTF8 -Value @(
        'param([switch]$RegisterVoices,[string]$GenerationList,[string]$DataRoot,[string]$MirrorSettings)',
        ('Set-Content -LiteralPath ''{0}'' -Encoding UTF8 -Value @($GenerationList,$DataRoot,$MirrorSettings)' -f $heard))
    foreach ($root in 'C:\', 'C:\Panthera data\with a trailing slash\', 'D:\plain') {
        $mirror = 'Phrasing=most;Inflection=40'
        $line = Format-ElevatedArguments $stub @('-RegisterVoices','-GenerationList','Tiger,Lion',
                    '-DataRoot',(ConvertTo-Argument $root),'-MirrorSettings',(ConvertTo-Argument $mirror))
        Remove-Item -LiteralPath $heard -ErrorAction SilentlyContinue
        $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -ArgumentList $line -Wait -PassThru -WindowStyle Hidden
        $got = Lines $heard
        Check ($run.ExitCode -eq 0 -and $got[0] -eq 'Tiger,Lion' -and $got[1] -eq $root -and $got[2] -eq $mirror) ("the elevated script is handed '" + $root + "' and the settings intact")
    }
    Check ((ConvertTo-Argument 'say "hi"\') -eq '"say \"hi\"\\"') 'a quote inside is escaped and a trailing backslash doubled'

    # And the elevated registration refuses a data folder that is not there,
    # before COM registration and before a token is touched: a broken path
    # used to remove every voice and add none.  The real dispatch block, with
    # its regsvr32 runs swapped for a note in a file, in a child process so
    # its `exit` ends that and not this test.  The note's exit code comes from
    # REGSVR_CODE, so a failing regsvr32 can be played too.
    $dispatch = @($ast.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.IfStatementAst] -and
        $_.Clauses[0].Item1.Extent.Text -match '\$RegisterVoices' })
    Check ($dispatch.Count -eq 1) 'there is one registration dispatch'
    Check ($dispatch[0].Extent.Text -match 'Invoke-Regsvr32' -and $dispatch[0].Extent.Text -notmatch '& .*regsvr32\.exe') 'registration runs regsvr32 only through Invoke-Regsvr32'
    $did = Join-Path $testDir 'did.txt'
    $guarded = Join-Path $testDir 'dispatch.ps1'
    Set-Content -LiteralPath $guarded -Encoding UTF8 -Value @(
        'param([switch]$RegisterVoices,[switch]$RegisterServer,[string]$DataRoot,[string]$MirrorSettings)',
        'function Grant-SettingsFolder { }', 'function Set-MachineSettings($s) { }',
        ('function Add-VoiceTokens($g) {{ Add-Content -LiteralPath ''{0}'' tokens }}' -f $did),
        ('function Get-ComServers { if ($env:NO_SERVERS) { return @() }; ' +
         '@(@{ Exe = ''a''; Dll = ''x86''; Required = $true }, @{ Exe = ''b''; Dll = ''x64''; Required = ($env:X64_REQUIRED -ne ''0'') }) }'),
        (('function Invoke-Regsvr32($Exe,$Dll,[switch]$Unregister) {{ Add-Content -LiteralPath ''{0}'' regsvr32; ' +
          'if ($Dll -eq ''x64'' -and $env:REGSVR_X64_CODE) {{ [int]$env:REGSVR_X64_CODE }} else {{ [int]$env:REGSVR_CODE }} }}') -f $did),
        '$Generations = @(''Tiger'')', $dispatch[0].Extent.Text)
    $env:REGSVR_CODE = '0'
    $missing = Join-Path $testDir 'no such folder\'
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $guarded @('-RegisterVoices','-DataRoot',(ConvertTo-Argument $missing)))
    Check ($run.ExitCode -eq 3 -and !(Test-Path -LiteralPath $did)) 'registering from a missing folder stops before regsvr32 and the tokens'
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $guarded @('-RegisterVoices','-DataRoot',(ConvertTo-Argument $testDir)))
    Check ($run.ExitCode -eq 0 -and (Lines $did).Count -eq 3) 'registering from a folder that is there goes ahead'
    # A regsvr32 that fails stops the run with its code, before any token
    # (panthera-speech#37): its failures used to go unseen.
    Remove-Item -LiteralPath $did
    $env:REGSVR_CODE = '5'
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $guarded @('-RegisterVoices','-DataRoot',(ConvertTo-Argument $testDir)))
    Check ($run.ExitCode -eq 5 -and @(Lines $did).Count -eq 1) 'a failing regsvr32 stops registration with its code, before the tokens'
    # A DLL that is not Required (x64 on ARM64) may fail; the rest goes on.
    Remove-Item -LiteralPath $did
    $env:REGSVR_CODE = '0'; $env:REGSVR_X64_CODE = '3'; $env:X64_REQUIRED = '0'
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $guarded @('-RegisterVoices','-DataRoot',(ConvertTo-Argument $testDir)))
    Check ($run.ExitCode -eq 0 -and (Lines $did).Count -eq 3) 'a DLL that is not required may fail, and the voices are still registered'
    Remove-Item -LiteralPath $did
    $env:X64_REQUIRED = '1'
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $guarded @('-RegisterVoices','-DataRoot',(ConvertTo-Argument $testDir)))
    Check ($run.ExitCode -eq 3 -and @(Lines $did).Count -eq 2) 'a required x64 DLL that fails stops registration, before the tokens'
    Remove-Item -LiteralPath $did
    $env:NO_SERVERS = '1'
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $guarded @('-RegisterVoices','-DataRoot',(ConvertTo-Argument $testDir)))
    Check ($run.ExitCode -eq 4 -and !(Test-Path -LiteralPath $did)) 'with no engine DLL to register, nothing is registered and it says so'
    Remove-Item Env:\REGSVR_CODE, Env:\REGSVR_X64_CODE, Env:\X64_REQUIRED, Env:\NO_SERVERS

    # Move voices for all users: the real -MigrateData block, in a child with
    # the registry, the settings mirror and the tokens swapped for nothing.
    # The voice bank it moved must stay readable by every account -- each
    # file inheriting the root's three entries -- while no account but an
    # administrator may write anywhere in the tree.  The grants used to go
    # down the tree with /T and left every FILE with an empty ACL: the moved
    # voices were unreadable, SYSTEM included, and went silent.
    $migrate = @($ast.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.IfStatementAst] -and
        $_.Clauses[0].Item1.Extent.Text -eq '$MigrateData' })
    Check ($migrate.Count -eq 1) 'there is one move dispatch'
    $moveFrom = Join-Path $testDir 'move from'
    $moveTo = Join-Path $testDir 'move to\macintalk'
    New-Item -ItemType Directory -Force -Path (Join-Path $moveFrom 'Tiger\voices') | Out-Null
    Set-Content -LiteralPath (Join-Path $moveFrom 'Tiger\voices\bank.bin') -Value 'a voice bank'
    $mover = Join-Path $testDir 'move.ps1'
    Set-Content -LiteralPath $mover -Encoding UTF8 -Value @(
        'param([switch]$MigrateData,[string]$MigrateFrom,[string]$DataRoot,[string]$MirrorSettings)',
        'function Set-MachineDataPath($p) { }', 'function Grant-SettingsFolder { }', 'function Set-MachineSettings($s) { }',
        'function Test-AnyPantheraTokens { $false }', 'function Add-VoiceTokens($g) { }',
        '$GenerationTable = @([pscustomobject]@{ Folder = ''Tiger'' })', $migrate[0].Extent.Text)
    $run = Microsoft.PowerShell.Management\Start-Process powershell.exe -Wait -PassThru -WindowStyle Hidden -ArgumentList (
        Format-ElevatedArguments $mover @('-MigrateData','-MigrateFrom',(ConvertTo-Argument $moveFrom),
                                          '-DataRoot',(ConvertTo-Argument $moveTo)))
    $bank = Join-Path $moveTo 'Tiger\voices\bank.bin'
    function UsersRights([string]$path) {
        @((Get-Acl -LiteralPath $path).Access | Where-Object {
            $_.AccessControlType -eq 'Allow' -and
            $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-32-545' } |
          ForEach-Object { $_.FileSystemRights.ToString() }) -join ','
    }
    Check ($run.ExitCode -eq 0 -and (Test-Path -LiteralPath $bank)) 'the move takes the generation folder'
    Check ((UsersRights $bank) -match 'Read') 'a moved voice bank is readable by every account (no empty ACL on its files)'
    Check ((UsersRights $moveTo) -notmatch 'Write|Modify|FullControl|CreateFiles|AppendData' -and
           (UsersRights $bank) -notmatch 'Write|Modify|FullControl') 'and only an administrator may write in the moved tree'
    & "$env:SystemRoot\System32\icacls.exe" (Join-Path $testDir 'move to') /reset /T /C /Q | Out-Null   # (removable again)

    # Which regsvr32 registers which DLL (panthera-speech#37).  32-bit Windows
    # has no SysWOW64 and no x64 DLL; a 32-bit PowerShell on 64-bit Windows
    # sees System32 redirected, so the x64 DLL goes through Sysnative.  Only
    # installed DLLs are listed, so the stage here has stand-in files.
    $fakeStage = Join-Path $testDir 'stage'
    foreach ($a in 'x86','x64') { New-Item -ItemType File -Force -Path (Join-Path $fakeStage "$a\panthera_sapi.dll") | Out-Null }
    $servers = @(Get-ComServers $false $false 'C:\TestWindows' $fakeStage $false)
    Check ($servers.Count -eq 1 -and $servers[0].Exe -eq 'C:\TestWindows\System32\regsvr32.exe' -and $servers[0].Dll -eq (Join-Path $fakeStage 'x86\panthera_sapi.dll') -and $servers[0].Required) '32-bit Windows registers the x86 DLL through System32, and nothing else'
    $servers = @(Get-ComServers $true $true 'C:\TestWindows' $fakeStage $false)
    Check ($servers.Count -eq 2 -and $servers[0].Exe -eq 'C:\TestWindows\SysWOW64\regsvr32.exe' -and $servers[1].Exe -eq 'C:\TestWindows\System32\regsvr32.exe' -and $servers[1].Dll -eq (Join-Path $fakeStage 'x64\panthera_sapi.dll') -and $servers[1].Required) '64-bit Windows registers x86 through SysWOW64 and x64 through System32, both required'
    $servers = @(Get-ComServers $true $false 'C:\TestWindows' $fakeStage $false)
    Check ($servers[1].Exe -eq 'C:\TestWindows\Sysnative\regsvr32.exe') 'a 32-bit PowerShell reaches the 64-bit regsvr32 through Sysnative'
    $servers = @(Get-ComServers $true $true 'C:\TestWindows' $fakeStage $true)
    Check ($servers.Count -eq 2 -and $servers[0].Required -and !$servers[1].Required) 'on ARM64 the x64 DLL is tried but not required'
    Remove-Item -LiteralPath (Join-Path $fakeStage 'x64\panthera_sapi.dll')
    $servers = @(Get-ComServers $true $true 'C:\TestWindows' $fakeStage $true)
    Check ($servers.Count -eq 1 -and $servers[0].Dll -like '*\x86\*') 'an x64 DLL that was not installed (ARM64 Windows 10) is not registered'
    $servers = @(Get-ComServers $true $true 'C:\TestWindows' $fakeStage $false)
    Check ($servers.Count -eq 2 -and $servers[1].Required) 'on x64 Windows a missing x64 DLL is still listed, so a damaged install fails loudly'
    Check ((Test-Arm64) -eq ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64')) 'Test-Arm64 reads this machine right'
    # And it is waited for, and what it says is heard.
    function Start-Process { param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru, $WindowStyle)
        $script:regsvr = @{ File = $FilePath; Args = $ArgumentList; Wait = [bool]$Wait }; [pscustomobject]@{ ExitCode = 3 } }
    Check ((Invoke-Regsvr32 'W:\r.exe' 'P:\x86\panthera_sapi.dll' -Unregister) -eq 3) 'regsvr32''s exit code is returned'
    Check ($regsvr.Wait -and $regsvr.File -eq 'W:\r.exe' -and $regsvr.Args -eq '/s /u "P:\x86\panthera_sapi.dll"') 'regsvr32 is waited for, silent, with the DLL quoted'
    function Start-Process { param([Parameter(ValueFromRemainingArguments=$true)]$Rest) throw 'The system cannot find the file specified.' }
    Check ((Invoke-Regsvr32 'W:\missing.exe' 'P:\x86\panthera_sapi.dll') -ne 0) 'a regsvr32 that will not start is a failure'
    Remove-Item Function:\Start-Process
    # The real regsvr32, on a DLL that is not there: nothing can be
    # registered, and "could not load" (3) only comes back if it is waited for.
    $code = Invoke-Regsvr32 (Join-Path $env:SystemRoot 'System32\regsvr32.exe') (Join-Path $testDir 'not there.dll')
    Check ($code -eq 3) "the real regsvr32 is waited for and its failure heard (got $code)"

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
    # Every access key on the form belongs to one control (panthera-speech#34).
    # A shared letter runs whichever control comes first after the focus:
    # Alt+U from Register ran Unregister, and Alt+D from Data location ticked
    # the log that reaches every account.
    $keys = @{}; $shared = @()
    foreach ($control in $form.Controls) {
        $m = [regex]::Match([string]$control.Text, '(?<!&)&([^&])')
        if (!$m.Success) { continue }
        $key = $m.Groups[1].Value.ToUpperInvariant()
        if ($keys.ContainsKey($key)) { $shared += ("Alt+$key = '" + $keys[$key] + "' and '" + $control.Text + "'") }
        else { $keys[$key] = $control.Text }
    }
    Check ($shared.Count -eq 0) ('every access key belongs to one control' + $(if ($shared) { ': ' + ($shared -join '; ') } else { '' }))
    Check ($keys.Count -ge 15) 'the access keys were found at all'

    # Saying No at the UAC prompt (panthera-speech#36).  -Verb RunAs gives no
    # process, `$null.ExitCode` is falsy, and Unregister said the voices were
    # gone and marked them declined for good.  Both ways a refusal arrives:
    # no process, and the error -ErrorAction Stop turns it into.
    $script:messages = @()
    function Show-Message([string]$Text, [string]$Icon = 'Information') { $script:messages += $Text }
    $list.Items.Clear()
    foreach ($generation in $GenerationTable) { [void]$list.Items.Add($generation.Item, $false) }
    $list.SetItemChecked(1, $true)
    $onClick = [Windows.Forms.Control].GetMethod('OnClick', [Reflection.BindingFlags]'NonPublic,Instance')
    foreach ($refusal in 'none', 'throws') {
        if ($refusal -eq 'none') {
            function Start-Process { param([Parameter(ValueFromRemainingArguments=$true)]$Rest) $null }
        } else {
            function Start-Process { param([Parameter(ValueFromRemainingArguments=$true)]$Rest) throw 'The operation was canceled by the user.' }
        }
        Check ($null -eq (Invoke-Elevated '-NoProfile')) ("a refused prompt ($refusal) is not an exit code")
        Set-DeclinedGenerations @('Tiger')
        $script:messages = @()
        [void]$onClick.Invoke($unregister, @([EventArgs]::Empty))
        Check ((@(Get-DeclinedGenerations) -join ',') -eq 'Tiger') ("Unregister refused ($refusal) marks nothing declined")
        Check ($messages.Count -eq 1 -and $messages[0] -like '*administrator permission was not given*') ("Unregister refused ($refusal) says so")
    }
    function Start-Process { param([Parameter(ValueFromRemainingArguments=$true)]$Rest) [pscustomobject]@{ ExitCode = 0 } }
    $script:messages = @()
    [void]$onClick.Invoke($unregister, @([EventArgs]::Empty))
    Check ((@(Get-DeclinedGenerations) -join ',') -eq 'Leopard,Tiger' -and $messages[0] -like '*were unregistered*') 'Unregister that ran marks the engine declined'
    Remove-Item Function:\Start-Process, Function:\Show-Message
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

    # Logging that reaches every account is said out loud at open.  The
    # machine file is the one every other account reads, so it is the one
    # that decides; the answer comes through $AskYesNo, so no dialog shows.
    $script:asked = 0; $script:answer = $true
    $AskYesNo = { param($text,$title) $script:asked++; $script:answer }
    Check ((Confirm-SharedLogging) -eq 'quiet' -and $asked -eq 0) 'no shared logging, no question'
    Set-SettingsFileValue $machineSettingsFile Diagnostics 1 | Out-Null
    $script:answer = $false
    Check ((Confirm-SharedLogging) -eq 'kept' -and $asked -eq 1 -and (Get-SettingsFileValue $machineSettingsFile Diagnostics 'DWord') -eq 1) 'answering no leaves the shared log on'
    $script:answer = $true
    $diagnostics.Checked = $true
    Check ((Confirm-SharedLogging) -eq 'off' -and $asked -eq 2) 'answering yes turns it off'
    Check (!$diagnostics.Checked -and (Get-SettingsFileValue $machineSettingsFile Diagnostics 'DWord') -eq 0 -and (Get-SettingsFileValue $userSettingsFile Diagnostics 'DWord') -eq 0) 'off reaches both files and the checkbox'
    Check ((Confirm-SharedLogging) -eq 'quiet' -and $asked -eq 2) 'once off, the question is not asked again'
} finally {
    if ($form) { $form.Dispose() }
    if ($extractTimer) { $extractTimer.Dispose() }
    # Only the unique test key and folder created above; never the real ones.
    Remove-Item -LiteralPath $testPath -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testDir -Recurse -Force -ErrorAction SilentlyContinue
}
