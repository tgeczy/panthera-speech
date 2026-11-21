"""Run the installer's actual first-install/upgrade decision in isolated Inno setups.

Only the registry locations and subprocess effects are redirected: no installed
SAPI tokens, COM classes, settings, or product files are touched.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")
ROOT = Path(__file__).resolve().parents[2]
ISCC = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe"

PROBE = r"""
param([switch]$RegisterVoices, [switch]$RegisterServer, [string]$GenerationList)
$ErrorActionPreference = 'Stop'
$Generations = @($GenerationList -split ',' | Where-Object { $_ })
$statePath = Join-Path $PSScriptRoot 'state.json'
$script:state = Get-Content -Raw $statePath | ConvertFrom-Json
$script:classes = 0; $script:added = 0
function Grant-SettingsFolder { }
function Set-MachineSettings($unused) { }
function Add-VoiceTokens($selected) {
    $script:added++
    $script:state.voices = @($selected)
}
$source = Get-Content -Raw -Encoding UTF8 '__SETTINGS__'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0] }
$dispatch = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Clauses[0].Item1.Extent.Text -match '\$RegisterVoices'
})
if ($dispatch.Count -ne 1) { throw 'registration dispatch missing or ambiguous' }
$text = $dispatch[0].Extent.Text
# Mock only the regsvr32 runs; retain the real dispatch, the real choice of
# which DLLs to register (Get-ComServers), settings calls, the Add-VoiceTokens
# call and exit-status checks. Return to the probe on exit 0.
if ($text -notmatch 'Invoke-Regsvr32') { throw 'expected the COM registration calls' }
$stage = $PSScriptRoot
# Get-ComServers lists only installed DLLs: stand-ins for the two a 64-bit
# install puts down.
foreach ($arch in 'x86','x64') {
    New-Item -ItemType File -Force -Path (Join-Path $stage "$arch\panthera_sapi.dll") | Out-Null
}
foreach ($fn in $ast.FindAll({ $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] -and
                               $args[0].Name -in 'Get-ComServers','Test-Arm64' }, $false)) {
    . ([scriptblock]::Create($fn.Extent.Text))
}
function Invoke-Regsvr32($Exe, $Dll, [switch]$Unregister) { $script:classes++; 0 }
$text = $text.Replace('exit 0', 'return')
& ([scriptblock]::Create($text))
@{voices=$script:state.voices; classes=$script:classes; added=$script:added} |
    ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $statePath
"""


@pytest.mark.parametrize("previous, voices", [
    (None, []),
    ("View32", ["Lion"]),
    ("View64", ["Lion"]),
    ("View64", []),
    ("View64", ["Tiger", "Snowleopard"]),
])
def test_installer_preserves_registration(tmp_path, previous, voices):
    if not ISCC.exists():
        pytest.skip("Inno Setup compiler not installed")
    import winreg

    installer = (ROOT / "sapi/installer.iss").read_text(encoding="utf8")
    code = installer.split("[Code]", 1)[1]
    key = "Software\\PantheraTests\\Installer-" + uuid.uuid4().hex
    # The real decision, including its InitializeSetup snapshot, reads two
    # isolated view keys. The harness writes View32 during installation, so
    # checking too late would break the fresh-install case.
    code = re.sub(r"UninstallKey = '[^']+';", lambda _: "UninstallKey = '" + key + "';", code)
    code = code.replace("RegKeyExists(HKLM32, UninstallKey)", "RegKeyExists(HKCU, UninstallKey + '\\View32')")
    code = code.replace("RegKeyExists(HKLM64, UninstallKey)", "RegKeyExists(HKCU, UninstallKey + '\\View64')")
    run = installer.split("[Run]", 1)[1].split("[UninstallDelete]", 1)[0]
    run = "\n".join(line for line in run.splitlines()
                    if line.startswith("Filename:") and "-Register" in line)
    assert len(run.splitlines()) == 2
    run = run.replace(r"{app}\settings.ps1", str(tmp_path / "probe.ps1"))
    (tmp_path / "probe.ps1").write_text(PROBE.replace("__SETTINGS__", str(ROOT / "sapi/settings.ps1")), encoding="utf-8-sig")
    # Use distinct objects, not just a count: a repair must leave identities
    # and custom token data unchanged, including all-unregistered state.
    tokens = [{"generation": g, "voice": "Alex", "dataPath": "D:/chosen data/" + g}
              for g in voices]
    (tmp_path / "state.json").write_text(json.dumps({"voices": tokens}), encoding="utf8")
    script = f"""[Setup]
AppName=Panthera isolated installer check
AppVersion=1
DefaultDirName={{tmp}}\\PantheraInstallerCheck
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
DisableWelcomePage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
OutputDir={tmp_path}
OutputBaseFilename=probe-setup
[Registry]
Root: HKCU; Subkey: "{key}\\View32"
[Run]
{run}
[Code]
{code}
"""
    iss = tmp_path / "probe.iss"
    iss.write_text(script, encoding="utf-8-sig")
    try:
        if previous:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + "\\" + previous):
                pass
        built = subprocess.run([str(ISCC), str(iss)], capture_output=True, text=True, timeout=90)
        assert built.returncode == 0, built.stdout + built.stderr
        log = tmp_path / "install.log"
        installed = subprocess.run([str(tmp_path / "probe-setup.exe"), "/VERYSILENT",
                                   "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART", "/LOG=" + str(log)],
                                  capture_output=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
        assert installed.returncode == 0, log.read_text(errors="replace")
        result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8-sig"))
        assert result["classes"] == 2, result
        if previous:
            assert result["voices"] == tokens, result
            assert result["added"] == 0, result
        else:
            assert result["voices"] == ["Tiger", "Leopard", "Snowleopard", "Lion"], result
            assert result["added"] == 1, result
    finally:
        # These exact, uniquely named test keys are the only registry writes.
        for leaf in ("View32", "View64"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key + "\\" + leaf)
            except FileNotFoundError:
                pass
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def test_installer_refuses_without_powershell_3(tmp_path):
    """No PowerShell 3, no install (panthera-speech#37): the settings tool
    that registers every voice cannot run on Windows 7's own PowerShell 2.0,
    and the install used to finish and register nothing, silently.  The real
    [Code], with the PowerShell key pointed at one no machine has."""
    if not ISCC.exists():
        pytest.skip("Inno Setup compiler not installed")
    installer = (ROOT / "sapi/installer.iss").read_text(encoding="utf8")
    code = installer.split("[Code]", 1)[1]
    assert "PowerShellKey = '" in code
    code = re.sub(r"PowerShellKey = '[^']+';",
                  lambda _: r"PowerShellKey = 'SOFTWARE\PantheraTests\NoPowerShell-" + uuid.uuid4().hex + "';", code)
    code = re.sub(r"UninstallKey = '[^']+';",
                  lambda _: r"UninstallKey = 'Software\PantheraTests\Installer-" + uuid.uuid4().hex + "';", code)
    ran = tmp_path / "ran.txt"
    # The one step that would show Setup went ahead: it writes a file.
    step = 'Filename: "cmd.exe"; Parameters: "/c echo ran> ""%s"""; Flags: runhidden' % ran
    script = "\n".join([
        "[Setup]",
        "AppName=Panthera isolated installer check",
        "AppVersion=1",
        r"DefaultDirName={tmp}\PantheraInstallerCheck",
        "CreateAppDir=no",
        "Uninstallable=no",
        "PrivilegesRequired=lowest",
        "OutputDir=%s" % tmp_path,
        "OutputBaseFilename=probe-setup",
        "[Run]",
        step,
        "[Code]",
        code])
    iss = tmp_path / "probe.iss"
    iss.write_text(script, encoding="utf-8-sig")
    built = subprocess.run([str(ISCC), str(iss)], capture_output=True, text=True, timeout=90)
    assert built.returncode == 0, built.stdout + built.stderr
    installed = subprocess.run([str(tmp_path / "probe-setup.exe"), "/VERYSILENT",
                               "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART"],
                              capture_output=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
    assert installed.returncode != 0, "Setup went ahead without PowerShell 3"
    assert not ran.exists(), "Setup ran its [Run] steps without PowerShell 3"
