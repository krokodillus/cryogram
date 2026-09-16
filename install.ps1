# Installs Cryogram for you alone, under your local app data, and starts it; nothing is installed system-wide.

# The one line: irm https://cryogram.app/install.ps1 | iex

# Run beside an unzipped copy of Cryogram it installs that copy; run on its own it downloads the latest release.

# One function, so nothing leaks into an iex session, and a failure is a throw, since exiting through iex closes the window
function Install-Cryogram {
  $ErrorActionPreference = 'Stop'
  # Windows PowerShell 5 downloads many times slower while drawing its progress bar, and may not offer TLS 1.2 unless told to
  $ProgressPreference = 'SilentlyContinue'
  try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

  $Repo = if ($env:CRYOGRAM_RELEASE_URL) { $env:CRYOGRAM_RELEASE_URL } else { 'https://github.com/krokodillus/cryogram' }
  # The one file read from the main branch: the Builder AI tab's model list, so it moves without a release
  $Raw = if ($env:CRYOGRAM_RAW_URL) { $env:CRYOGRAM_RAW_URL } else { 'https://raw.githubusercontent.com/krokodillus/cryogram/main' }
  $ClaudePage = 'https://docs.claude.com/en/docs/claude-code/setup'
  $CodexPage = 'https://developers.openai.com/codex/cli'
  $Launcher = 'start.bat'

  function Say($text) { Write-Host $text }
  function Fail($text) { Write-Host "Cryogram was not installed. $text" -ForegroundColor Red; throw 'Cryogram was not installed.' }

  $Dir = if ($env:CRYOGRAM_INSTALL_DIR) { $env:CRYOGRAM_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'Programs\Cryogram' }
  # A folder you cloned yourself is yours to update with git
  if (Test-Path (Join-Path $Dir '.git')) { Fail "$Dir is a git clone. Update it with git pull instead." }

  # The copy beside this script, when the script is a file and Cryogram is unzipped around it; through iex there is no file
  $here = ''
  if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'boot.py')) -and (Test-Path (Join-Path $PSScriptRoot 'backend\server.py'))) {
    $here = (Resolve-Path $PSScriptRoot).Path
  }
  if ($here -and (Test-Path $Dir) -and ($here -eq (Resolve-Path $Dir).Path)) {
    Fail "$here is already the installed copy. To update it, run the install line instead."
  }

  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("cryogram-install-" + [System.IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $unpacked = Join-Path $tmp 'unpacked'
  New-Item -ItemType Directory -Path $unpacked | Out-Null

  try {
    if ($here) {
      $config = Get-Content (Join-Path $here 'backend\config.py') -Raw
      if ($config -notmatch '(?m)^VERSION = "([^"]+)"') { Fail "$here does not have the shape of a Cryogram release." }
      $version = $Matches[1]
      Say ""
      Say "Installing Cryogram $version from $here..."
      $newPath = Join-Path $unpacked "cryogram-$version"
      Copy-Item -Path $here -Destination $newPath -Recurse
      $new = Get-Item $newPath
    } else {
      # The repository's API answers with the latest tag
      $api = $Repo -replace '^https://github\.com/', 'https://api.github.com/repos/'
      try {
        $release = Invoke-RestMethod -Uri "$api/releases/latest" -UseBasicParsing
      } catch {
        Fail "Could not reach $Repo to find the latest release."
      }
      $tag = "$($release.tag_name)"
      if ($tag -notmatch '^v[0-9]') { Fail "Could not tell the latest release from $Repo (got '$tag')." }
      $version = $tag.Substring(1)
      Say ""
      Say "Downloading Cryogram $version from $Repo..."
      $zip = Join-Path $tmp 'cryogram.zip'
      try {
        Invoke-WebRequest -Uri "$Repo/archive/refs/tags/$tag.zip" -OutFile $zip -UseBasicParsing
      } catch {
        Fail "The download failed."
      }
      Expand-Archive -Path $zip -DestinationPath $unpacked
      # GitHub's archive holds one folder named after the repository and the tag
      $new = Get-ChildItem -Path $unpacked -Directory | Select-Object -First 1
      if (-not $new -or -not (Test-Path (Join-Path $new.FullName 'boot.py')) -or -not (Test-Path (Join-Path $new.FullName 'backend\server.py'))) {
        Fail "The download does not have the shape of a Cryogram release."
      }
    }

    # Today's model list for the Builder AI tab, from the main branch; when that read fails the release's own copy stays
    try {
      Invoke-WebRequest -Uri "$Raw/builder-models.json" -OutFile (Join-Path $new.FullName 'builder-models.json') -UseBasicParsing
    } catch { }

    # A copy already here: ask a running one to stop, then carry its private Python and its environment over
    $oldBoot = Join-Path $Dir 'boot.py'
    if (Test-Path $oldBoot) {
      # The private Python if there is one, else the py launcher, which never resolves to the Store's placeholder
      $oldPy = Join-Path $Dir 'python\python.exe'
      $oldArgs = @()
      if (-not (Test-Path $oldPy)) {
        $oldPy = (Get-Command py -ErrorAction SilentlyContinue).Source
        $oldArgs = @('-3')
      }
      if ($oldPy) {
        & $oldPy @oldArgs $oldBoot status *> $null
        if ($LASTEXITCODE -eq 0) {
          Say ""
          Say "Cryogram is running, asking it to stop..."
          & $oldPy @oldArgs $oldBoot stop *> $null
          if ($LASTEXITCODE -ne 0) {
            Fail "Cryogram did not stop, so the files were left as they are. Quit it from the bottom of the app's sidebar, then run this again."
          }
        }
      }
      foreach ($keep in @('python', '.venv')) {
        $from = Join-Path $Dir $keep
        if (Test-Path $from) { Move-Item $from (Join-Path $new.FullName $keep) }
      }
      Remove-Item -Recurse -Force $Dir
      Say ""
      Say "The previous version was replaced."
    }
    New-Item -ItemType Directory -Path (Split-Path $Dir -Parent) -Force | Out-Null
    Move-Item $new.FullName $Dir
  } finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  }
  Say ""
  Say "Cryogram $version is in $Dir."

  # Whether Cryogram gets a shortcut; the answer is remembered, and CRYOGRAM_ICON=yes or no answers for a script
  $icon = if ($env:CRYOGRAM_ICON) { $env:CRYOGRAM_ICON } else { '' }
  if (-not $icon) {
    Say ""
    $answer = Read-Host "Add Cryogram to the Start menu and the desktop? [Y/n]"
    $icon = if ($answer -match '^[nN]') { 'no' } else { 'yes' }
  }
  if ($icon -eq 'no') {
    $setupFlag = '--no-icon'
    $startFrom = "by double-clicking `"$Launcher`" in $Dir, or by typing cryogram in a terminal"
  } else {
    $setupFlag = '--icon'
    $startFrom = 'from the Start menu'
  }

  # The environment before the builder question, so a window closed early still leaves a working install
  Say ""
  Say "Setting up Cryogram's own environment (a minute or two)..."
  Push-Location $Dir
  try { & cmd /c (Join-Path $Dir $Launcher) setup $setupFlag } finally { Pop-Location }
  Say ""
  if ($LASTEXITCODE -ne 0) { Say "The setup did not finish. Start Cryogram by double-clicking `"$Launcher`" in $Dir to try again." }
  elseif ($icon -eq 'no') { Say "The command cryogram works in a new terminal." }
  else { Say "Cryogram is in your Start menu and on your desktop now, and the command cryogram works in a new terminal." }

  # The program the Builder Agent runs on has to be on this computer, sign-in or API key alike; the setup screen checks it
  function Present($name) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { return $true }
    foreach ($c in @((Join-Path $env:APPDATA "npm\$name.cmd"), (Join-Path $env:LOCALAPPDATA "Programs\$name\$name.exe"), (Join-Path $env:USERPROFILE ".local\bin\$name.exe"))) {
      if (Test-Path $c) { return $true }
    }
    return $false
  }
  function State($name) { if (Present $name) { 'installed' } else { 'not installed' } }
  function OpenPage($url) {
    Say ""
    Say "Opening $url"
    if (-not $env:CRYOGRAM_NO_BROWSER) { Start-Process $url }
  }
  $choice = if ($env:CRYOGRAM_BUILDER) { $env:CRYOGRAM_BUILDER } else { '' }
  if (-not $choice) {
    Say ""
    Say "Cryogram's Builder Agent runs on one of two programs on this computer:"
    Say "  1. Claude Code, with a Claude subscription or an Anthropic API key  ($(State claude))"
    Say "  2. Codex, with a ChatGPT subscription or an OpenAI API key          ($(State codex))"
    $answer = Read-Host "Which will you use? [1/2, or Enter to decide later in the app]"
    if ($answer -eq '1') { $choice = 'claude' } elseif ($answer -eq '2') { $choice = 'codex' }
  }
  $missing = ''
  if ($choice -eq 'claude') {
    if (Present claude) { Say "Claude Code is installed." }
    else {
      $missing = 'Claude Code'
      Say ""
      Say "Claude Code is not installed yet. Install it from the page opening in your browser, sign in there, and Cryogram's setup screen will check the connection."
      OpenPage $ClaudePage
    }
  } elseif ($choice -eq 'codex') {
    if (Present codex) { Say "Codex is installed." }
    else {
      $missing = 'Codex'
      Say ""
      Say "Codex is not installed yet. Install it from the page opening in your browser, sign in there, and Cryogram's setup screen will check the connection."
      OpenPage $CodexPage
    }
  }

  if ($env:CRYOGRAM_NO_START) {
    Say ""
    Say "Start it by double-clicking `"$Launcher`" in $Dir."
    return
  }
  Say ""
  if ($missing) {
    Say "You can close this window and start Cryogram $startFrom, or continue here when you have installed $missing."
    Read-Host "Press Enter when you have logged in to $missing to complete the setup" | Out-Null
  } else {
    Read-Host "Press Enter to start Cryogram now, or close this window and start it $startFrom later" | Out-Null
  }
  Set-Location $Dir
  & cmd /c (Join-Path $Dir $Launcher)
}

Install-Cryogram
