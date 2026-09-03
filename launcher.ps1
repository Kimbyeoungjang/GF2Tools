param(
    [ValidateSet('start', 'reset', 'diagnose')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot

$RuntimeRoot = Join-Path $PSScriptRoot '.gfl2_runtime'
$DiscoveryLog = Join-Path $RuntimeRoot 'python-discovery.log'
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

$script:Attempts = New-Object System.Collections.Generic.List[string]

function Write-Discovery([string]$Message) {
    $script:Attempts.Add($Message)
}

function Resolve-Executable([string]$Command) {
    if ([string]::IsNullOrWhiteSpace($Command)) { return $null }
    try {
        if (Test-Path -LiteralPath $Command -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Command).Path
        }
        $cmd = Get-Command $Command -ErrorAction Stop | Select-Object -First 1
        if ($cmd -and $cmd.Source) { return $cmd.Source }
        if ($cmd -and $cmd.Path) { return $cmd.Path }
    } catch {}
    return $null
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory=$true)][string]$Executable,
        [string[]]$PrefixArgs = @(),
        [string]$Label = ''
    )

    $resolved = Resolve-Executable $Executable
    if (-not $resolved) {
        Write-Discovery("MISS  $Label :: $Executable $($PrefixArgs -join ' ')")
        return $null
    }

    # Keep every comparison inside Python. No CMD metacharacters are involved here.
    $probe = "import sys; ok=((3,11) <= sys.version_info[:2] < (3,14) and sys.maxsize > 4294967296); print(sys.executable); print(sys.version.split()[0]); print('64' if sys.maxsize > 4294967296 else '32'); raise SystemExit(0 if ok else 17)"
    try {
        $output = @(& $resolved @PrefixArgs -c $probe 2>$null)
        $rc = $LASTEXITCODE
    } catch {
        Write-Discovery("FAIL  $Label :: $resolved $($PrefixArgs -join ' ') :: $($_.Exception.Message)")
        return $null
    }

    if ($rc -eq 0 -and $output.Count -ge 1) {
        $realExe = [string]$output[0]
        $version = if ($output.Count -ge 2) { [string]$output[1] } else { '?' }
        $bits = if ($output.Count -ge 3) { [string]$output[2] } else { '?' }
        Write-Discovery("OK    $Label :: $resolved $($PrefixArgs -join ' ') -> $realExe Python $version ${bits}-bit")
        # Once the probe succeeds, invoke the concrete interpreter reported by
        # sys.executable. This avoids depending on PATH or py.exe again later.
        $concreteExe = Resolve-Executable $realExe
        if (-not $concreteExe) { $concreteExe = $resolved }
        $concreteArgs = if ($concreteExe -eq $resolved) { @($PrefixArgs) } else { @() }
        return [pscustomobject]@{
            Exe = $concreteExe
            Args = @($concreteArgs)
            RealExe = $realExe
            Version = $version
            Bits = $bits
            Label = $Label
        }
    }

    Write-Discovery("REJECT $Label :: $resolved $($PrefixArgs -join ' ') :: exit=$rc")
    return $null
}

function Find-SupportedPython {
    # 1) Reuse a Python interpreter that already belongs to this project if one exists.
    $currentFile = Join-Path $RuntimeRoot 'current.txt'
    if (Test-Path -LiteralPath $currentFile) {
        try {
            $name = (Get-Content -LiteralPath $currentFile -Raw).Trim()
            if ($name -and ([IO.Path]::GetFileName($name) -eq $name)) {
                $candidate = Join-Path (Join-Path $RuntimeRoot $name) 'Scripts\python.exe'
                $hit = Test-PythonCandidate -Executable $candidate -Label 'current project runtime'
                if ($hit) { return $hit }
            }
        } catch {}
    }


    # 2) Typical per-user and all-user CPython install locations.
    foreach ($ver in @('313','312','311')) {
        if ($env:LOCALAPPDATA) {
            $hit = Test-PythonCandidate -Executable (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$ver\python.exe") -Label "LocalAppData Python$ver"
            if ($hit) { return $hit }
        }
        if ($env:ProgramFiles) {
            $hit = Test-PythonCandidate -Executable (Join-Path $env:ProgramFiles "Python$ver\python.exe") -Label "ProgramFiles Python$ver"
            if ($hit) { return $hit }
        }
        $hit = Test-PythonCandidate -Executable "C:\Python$ver\python.exe" -Label "C:\Python$ver"
        if ($hit) { return $hit }
    }

    # 3) Python Launcher. This is more reliable than PATH on many Windows installs.
    $launcherCandidates = New-Object System.Collections.Generic.List[string]
    if ($env:LOCALAPPDATA) { $launcherCandidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe')) }
    if ($env:WINDIR) { $launcherCandidates.Add((Join-Path $env:WINDIR 'py.exe')) }
    $launcherCandidates.Add('py.exe')
    foreach ($launcher in $launcherCandidates) {
        foreach ($selector in @('-3.13','-3.12','-3.11')) {
            $hit = Test-PythonCandidate -Executable $launcher -PrefixArgs @($selector) -Label "Python Launcher $selector"
            if ($hit) { return $hit }
        }
    }

    # 4) PATH aliases/direct executables. The Microsoft Store alias is rejected by the probe if unusable.
    foreach ($command in @('python.exe','python','python3.exe','python3')) {
        $hit = Test-PythonCandidate -Executable $command -Label "PATH $command"
        if ($hit) { return $hit }
    }

    # 5) Registered CPython installations. Search HKCU and both HKLM registry views.
    $registryRoots = @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )
    foreach ($regRoot in $registryRoots) {
        if (-not (Test-Path $regRoot)) { continue }
        foreach ($sub in @(Get-ChildItem $regRoot -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending)) {
            try {
                $installKey = Get-Item (Join-Path $sub.PSPath 'InstallPath') -ErrorAction Stop
                $installDir = [string]$installKey.GetValue('')
                if ($installDir) {
                    $hit = Test-PythonCandidate -Executable (Join-Path $installDir 'python.exe') -Label "Registry $($sub.PSChildName)"
                    if ($hit) { return $hit }
                }
            } catch {}
        }
    }

    # 6) Last-resort shallow scan of the standard per-user Python folder.
    if ($env:LOCALAPPDATA) {
        $pythonRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
        if (Test-Path $pythonRoot) {
            foreach ($pattern in @('Python31*\python.exe','*\python.exe')) {
                foreach ($exe in @(Get-ChildItem -Path (Join-Path $pythonRoot $pattern) -File -ErrorAction SilentlyContinue)) {
                    $hit = Test-PythonCandidate -Executable $exe.FullName -Label 'LocalAppData scan'
                    if ($hit) { return $hit }
                }
            }
        }
    }
    return $null
}

Write-Host '[GFL2 Tools] Finding Python 3.11 - 3.13 64-bit...'
$python = Find-SupportedPython

try {
    @(
        "GFL2 Tools Python discovery $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "User: $env:USERNAME",
        "PowerShell: $($PSVersionTable.PSVersion)",
        "Project: $PSScriptRoot",
        ''
    ) + $script:Attempts | Set-Content -LiteralPath $DiscoveryLog -Encoding UTF8
} catch {}

if (-not $python) {
    Write-Host ''
    Write-Host '[GFL2 Tools] A supported Python interpreter could not be located.' -ForegroundColor Red
    Write-Host '[GFL2 Tools] This launcher checked PATH, py.exe, common install folders, the registry,'
    Write-Host '[GFL2 Tools] and previous GFL2 Tools runtimes.'
    Write-Host "[GFL2 Tools] Diagnostic log: $DiscoveryLog"
    Write-Host ''
    Write-Host '[GFL2 Tools] Do not run as Administrator just to start the program.'
    Write-Host '[GFL2 Tools] If Python is installed only for your Windows account, launch normally.'
    exit 2
}

Write-Host "[GFL2 Tools] Using Python $($python.Version) $($python.Bits)-bit:"
Write-Host "  $($python.RealExe)"

if ($Action -eq 'diagnose') { exit 0 }

$bootstrap = Join-Path $PSScriptRoot 'bootstrap.py'
$bootstrapArgs = @($bootstrap)
switch ($Action) {
    'reset'  { $bootstrapArgs += @('--reset','--no-launch') }
}

try {
    $invokeArgs = @()
    $invokeArgs += @($python.Args)
    $invokeArgs += $bootstrapArgs
    & $python.Exe @invokeArgs
    $rc = $LASTEXITCODE
} catch {
    Write-Host "[GFL2 Tools] Failed to start bootstrap.py: $($_.Exception.Message)" -ForegroundColor Red
    exit 3
}
if ($null -eq $rc) { $rc = 1 }
exit $rc
