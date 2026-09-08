param([Parameter(Mandatory=$true)][string]$OutputDirectory)
# Exercise installed DLLs through real SAPI tokens. Run once from each
# PowerShell bitness. Generated audio belongs only in the caller's test folder.
# Preferences, registration and the system default voice are never changed.
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$speech = New-Object -ComObject SAPI.SpVoice
try {
    $speech.Rate = 0
    $speech.Volume = 100
    $voices = $speech.GetVoices('Vendor=Panthera Speech')
    $count = 0
    for ($i=0; $i -lt $voices.Count; $i++) {
        $voice = $voices.Item($i)
        $name = $voice.GetAttribute('Name')
        if ($name -notmatch '^(Alex|Vicki|Fred) \(') { continue }
        # Later generations' formant Fred is not a deterministic oracle.
        if ($name.StartsWith('Fred') -and $name -ne 'Fred (Tiger)') { continue }
        $speech.Voice = $voice
        $hash = $null
        foreach ($repeat in 1,2) {
            $path = Join-Path $OutputDirectory (($name -replace '[^a-zA-Z0-9]','_') + "-$repeat.wav")
            $stream = New-Object -ComObject SAPI.SpFileStream
            try {
                $stream.Open($path,3,$false)
                $speech.AudioOutputStream = $stream
                [void]$speech.Speak('Restart with debug logging enabled.',0)
            } finally {
                $stream.Close()
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($stream)
            }
            if ((Get-Item $path).Length -lt 8000) { throw "$name returned truncated audio" }
            $current = (Get-FileHash -Algorithm SHA256 $path).Hash
            if ($hash -and $hash -ne $current) { throw "$name changed on the warm request" }
            $hash = $current
        }
        Write-Output "PASS $name $hash"
        $count++
    }
    if ($count -ne 8) { throw "Expected 8 installed voice controls, found $count" }
    Write-Output "PASS: $count registered tokens, cold/warm identical, $([IntPtr]::Size*8)-bit SAPI"
} finally {
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($speech)
}
