[CmdletBinding()]
param(
    [switch] $Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$KeepCount = 7
$BackupDirectory = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'BACKUP\敏感文件'
$TemporaryZip = $null
$TemporaryEncrypted = $null

function Show-Usage {
    @'
Usage:
  powershell.exe -ExecutionPolicy Bypass -File .\backup-administrator-sensitive.ps1

Creates a GPG AES-256 encrypted ZIP archive in ..\..\BACKUP\敏感文件 relative to this script.
GPG prompts for a passphrase. The latest seven successful archives are kept.

Inspect:
  gpg.exe --output archive.zip --decrypt administrator-sensitive-YYYYmmdd-HHmmss.zip.gpg

Extract into a staging directory first:
  Expand-Archive -LiteralPath .\archive.zip -DestinationPath .\restore
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

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
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

function Add-DirectoryToZip {
    param(
        [string] $SourcePath,
        [string] $ArchivePath,
        [System.IO.Compression.ZipArchive] $Archive
    )

    $archiveRoot = $ArchivePath.TrimEnd('/')
    [void] $Archive.CreateEntry("$archiveRoot/")

    foreach ($item in Get-ChildItem -LiteralPath $SourcePath -Force -Recurse) {
        $relativePath = $item.FullName.Substring($SourcePath.Length).TrimStart('\')
        $entryPath = "$archiveRoot/$($relativePath -replace '\\', '/')"

        if ($item.PSIsContainer) {
            [void] $Archive.CreateEntry("$($entryPath.TrimEnd('/'))/")
            continue
        }

        try {
            $entry = $Archive.CreateEntry($entryPath, [System.IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = $item.LastWriteTime
            $input = [System.IO.File]::Open($item.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
            try {
                $output = $entry.Open()
                try {
                    $input.CopyTo($output)
                }
                finally {
                    $output.Dispose()
                }
            }
            finally {
                $input.Dispose()
            }
        }
        catch {
            Write-Warning "Skipping unreadable file $($item.FullName): $($_.Exception.Message)"
        }
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Stop-Backup 'this script must run on Windows'
}

if (-not (Test-Administrator)) {
    Stop-Backup 'this script must run from an elevated Administrator PowerShell session'
}

$gpgCommand = Get-Command 'gpg.exe' -ErrorAction SilentlyContinue
if ($null -eq $gpgCommand) {
    Stop-Backup 'gpg.exe is required but was not found in PATH'
}

if (-not (Test-Path -LiteralPath $BackupDirectory)) {
    [void] (New-Item -ItemType Directory -Path $BackupDirectory -Force)
}
Set-PrivateDirectoryAcl -Path $BackupDirectory

$SourcePaths = @(
    @{ Path = 'C:\Users\Administrator\.ssh'; ArchivePath = 'Administrator/.ssh' }
    @{ Path = 'C:\Users\Administrator\.gnupg'; ArchivePath = 'Administrator/.gnupg' }
    @{ Path = 'C:\Users\Administrator\.aws'; ArchivePath = 'Administrator/.aws' }
    @{ Path = 'C:\Users\Administrator\.azure'; ArchivePath = 'Administrator/.azure' }
    @{ Path = 'C:\Users\Administrator\.kube'; ArchivePath = 'Administrator/.kube' }
    @{ Path = 'C:\Users\Administrator\AppData\Roaming\Microsoft\Credentials'; ArchivePath = 'Administrator/AppData/Roaming/Microsoft/Credentials' }
    @{ Path = 'C:\Users\Administrator\AppData\Local\Microsoft\Credentials'; ArchivePath = 'Administrator/AppData/Local/Microsoft/Credentials' }
    @{ Path = 'C:\Users\Administrator\AppData\Roaming\Microsoft\Protect'; ArchivePath = 'Administrator/AppData/Roaming/Microsoft/Protect' }
    @{ Path = 'C:\Users\Administrator\AppData\Local\Microsoft\Vault'; ArchivePath = 'Administrator/AppData/Local/Microsoft/Vault' }
    @{ Path = 'C:\ProgramData\ssh'; ArchivePath = 'ProgramData/ssh' }
)

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$TemporaryZip = Join-Path $BackupDirectory ('.administrator-sensitive-{0}.zip' -f [guid]::NewGuid().ToString('N'))
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$finalArchive = Join-Path $BackupDirectory "administrator-sensitive-$timestamp.zip.gpg"
$TemporaryEncrypted = Join-Path $BackupDirectory ('.administrator-sensitive-encrypted-{0}' -f [guid]::NewGuid().ToString('N'))

try {
    $fileStream = [System.IO.File]::Open($TemporaryZip, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    try {
        $archive = New-Object System.IO.Compression.ZipArchive($fileStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            $included = 0
            foreach ($source in $SourcePaths) {
                if (Test-Path -LiteralPath $source.Path -PathType Container) {
                    Write-Log "Including $($source.Path)"
                    Add-DirectoryToZip -SourcePath $source.Path -ArchivePath $source.ArchivePath -Archive $archive
                    $included++
                }
                else {
                    Write-Log "Skipping missing path: $($source.Path)"
                }
            }

            if ($included -eq 0) {
                Stop-Backup 'none of the configured sensitive paths exists'
            }
        }
        finally {
            $archive.Dispose()
        }
    }
    finally {
        $fileStream.Dispose()
    }

    if (Test-Path -LiteralPath $finalArchive) {
        Stop-Backup "refusing to overwrite existing backup: $finalArchive"
    }

    Write-Log 'Encrypting archive; GPG will request a passphrase'
    & $gpgCommand.Source --yes --symmetric --cipher-algo AES256 --output $TemporaryEncrypted $TemporaryZip
    if ($LASTEXITCODE -ne 0) {
        Stop-Backup "gpg.exe encryption failed with exit code $LASTEXITCODE"
    }

    Move-Item -LiteralPath $TemporaryEncrypted -Destination $finalArchive
    $TemporaryEncrypted = $null

    $archives = @(Get-ChildItem -LiteralPath $BackupDirectory -Filter 'administrator-sensitive-*.zip.gpg' -File | Sort-Object Name)
    if ($archives.Count -gt $KeepCount) {
        $archives | Select-Object -First ($archives.Count - $KeepCount) | ForEach-Object {
            Write-Log "Removing old backup: $($_.FullName)"
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }

    Write-Log "Backup created: $finalArchive"
}
finally {
    if ($null -ne $TemporaryZip -and (Test-Path -LiteralPath $TemporaryZip)) {
        Remove-Item -LiteralPath $TemporaryZip -Force
    }
    if ($null -ne $TemporaryEncrypted -and (Test-Path -LiteralPath $TemporaryEncrypted)) {
        Remove-Item -LiteralPath $TemporaryEncrypted -Force
    }
}
