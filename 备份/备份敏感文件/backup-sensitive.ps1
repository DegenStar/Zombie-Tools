[CmdletBinding()]
param(
    [switch] $Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$KeepCount = 7
$BackupDirectory = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'BACKUP\敏感文件'
$TemporaryTarGz = $null

function Show-Usage {
    @'
Usage:
  powershell.exe -ExecutionPolicy Bypass -File .\backup-sensitive.ps1

Creates a tar.gz archive in ..\..\BACKUP\敏感文件 relative to this script.
The latest seven successful archives are kept.

Inspect:
  tar.exe -tzf .\administrator-sensitive-YYYYmmdd-HHmmss.tar.gz

Extract into a staging directory first:
  tar.exe -xzf .\archive.tar.gz -C .\restore
'@
}

function Write-Log {
    param([string] $Message)
    Write-Host "[backup] $Message"
}

function Stop-Backup {
    param([string] $Message)
    throw "[backup] ERROR: $Message"
}

function Set-PrivateDirectoryAcl {
    param([string] $Path)

    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)

    $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $administrators = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')

    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($currentUser, 'FullControl', $inheritance, $propagation, $allow)))
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($administrators, 'FullControl', $inheritance, $propagation, $allow)))
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function New-TarGzArchive {
    param(
        [string] $ArchivePath,
        [array] $Sources
    )

    $tar = Get-Command tar.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $tar) {
        Stop-Backup 'tar.exe was not found; install or enable the Windows tar utility'
    }

    $arguments = @('-c', '-z', '-f', $ArchivePath)
    foreach ($source in $Sources) {
        $arguments += '-C'
        $arguments += $source.TarWorkingDirectory
        $arguments += $source.ArchivePath
    }

    $tarOutput = & $tar.Source @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $details = ($tarOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        Stop-Backup "tar.exe failed with exit code ${LASTEXITCODE}: $details"
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Stop-Backup 'this script must run on Windows'
}

if (-not (Test-Path -LiteralPath $BackupDirectory)) {
    [void] (New-Item -ItemType Directory -Path $BackupDirectory -Force)
}
try {
    Set-PrivateDirectoryAcl -Path $BackupDirectory
}
catch {
    Write-Warning 'Could not restrict backup directory ACL; continuing with current permissions'
}

$CurrentUserProfile = $env:USERPROFILE
if ([string]::IsNullOrWhiteSpace($CurrentUserProfile) -or -not (Test-Path -LiteralPath $CurrentUserProfile -PathType Container)) {
    Stop-Backup 'the current user profile directory could not be determined'
}
$CurrentUserArchiveRoot = Split-Path -Leaf $CurrentUserProfile.TrimEnd('\')
$CurrentUserArchiveParent = Split-Path -Parent $CurrentUserProfile

$SourcePaths = @(
    @{ Path = Join-Path $CurrentUserProfile '.ssh'; ArchivePath = "$CurrentUserArchiveRoot/.ssh"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile '.gnupg'; ArchivePath = "$CurrentUserArchiveRoot/.gnupg"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile '.aws'; ArchivePath = "$CurrentUserArchiveRoot/.aws"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile '.azure'; ArchivePath = "$CurrentUserArchiveRoot/.azure"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile '.kube'; ArchivePath = "$CurrentUserArchiveRoot/.kube"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile 'AppData\Roaming\Microsoft\Credentials'; ArchivePath = "$CurrentUserArchiveRoot/AppData/Roaming/Microsoft/Credentials"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile 'AppData\Local\Microsoft\Credentials'; ArchivePath = "$CurrentUserArchiveRoot/AppData/Local/Microsoft/Credentials"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile 'AppData\Roaming\Microsoft\Protect'; ArchivePath = "$CurrentUserArchiveRoot/AppData/Roaming/Microsoft/Protect"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = Join-Path $CurrentUserProfile 'AppData\Local\Microsoft\Vault'; ArchivePath = "$CurrentUserArchiveRoot/AppData/Local/Microsoft/Vault"; TarWorkingDirectory = $CurrentUserArchiveParent }
    @{ Path = 'C:\ProgramData\ssh'; ArchivePath = 'ProgramData/ssh'; TarWorkingDirectory = 'C:\' }
)

$TemporaryTarGz = Join-Path $BackupDirectory ('.administrator-sensitive-{0}.tar.gz' -f [guid]::NewGuid().ToString('N'))
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$finalArchive = Join-Path $BackupDirectory "administrator-sensitive-$timestamp.tar.gz"

try {
    $includedSources = @()
    foreach ($source in $SourcePaths) {
        if (Test-Path -LiteralPath $source.Path -PathType Container) {
            Write-Log "Including $($source.Path)"
            $includedSources += $source
        }
        else {
            Write-Log "Skipping missing path: $($source.Path)"
        }
    }

    if ($includedSources.Count -eq 0) {
        Stop-Backup 'none of the configured sensitive paths exists'
    }

    New-TarGzArchive -ArchivePath $TemporaryTarGz -Sources $includedSources

    if (Test-Path -LiteralPath $finalArchive) {
        Stop-Backup "refusing to overwrite existing backup: $finalArchive"
    }

    Move-Item -LiteralPath $TemporaryTarGz -Destination $finalArchive
    $TemporaryTarGz = $null

    $archives = @(Get-ChildItem -LiteralPath $BackupDirectory -Filter 'administrator-sensitive-*.tar.gz' -File | Sort-Object Name)
    if ($archives.Count -gt $KeepCount) {
        $archives | Select-Object -First ($archives.Count - $KeepCount) | ForEach-Object {
            Write-Log "Removing old backup: $($_.FullName)"
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }

    Write-Log "Backup created: $finalArchive"
}
finally {
    if ($null -ne $TemporaryTarGz -and (Test-Path -LiteralPath $TemporaryTarGz)) {
        Remove-Item -LiteralPath $TemporaryTarGz -Force
    }
}
