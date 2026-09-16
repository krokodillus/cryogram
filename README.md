# Cryogram

Cryogram figures out a workflow by letting an AI agent do it once, then writes it into code and tests it for you. Integrate with any service that allows you to, and if not, run it in an automated browser. Use AI only for specific steps where needed.

The AI agent can run on your existing Claude or ChatGPT/Codex subscription - no additional login required, or if you prefer, via an API key. Most workflows don't need any AI once they have been built, but the ones that do need an API key. Most model providers are supported.

Cryogram is currently in beta - this means you might hit into some unexpected issues here and there when building, so we recommend testing your workflows a few times before relying on them blindly.

Once a workflow is up and running, there are checks to catch problems and stop it if something goes wrong. Cryogram will store a snapshot of your data until the AI agent has fixed the problem, so you can continue the run from where it stopped instead of starting over again.

The user interface is in your browser while Cryogram runs on a Python server in the background. Cryogram keeps running in the background if you close the browser: click **Quit** in the menu bar to also shut down the server.

This repository holds Cryogram's source code, published with each release. Found a problem? Open an issue here, or write to us at https://cryogram.app/support

## Installing

Run the line below in your terminal to download and install the latest version directly from the repository. It will install into its own folder, which you can safely delete if you change your mind.

- Mac: `curl -fsSL https://cryogram.app/install.sh | sh`
- Windows, in PowerShell: `irm https://cryogram.app/install.ps1 | iex`

The installer downloads the source code for the latest GitHub release. It installs into `~/Applications/Cryogram` on a Mac or `%LOCALAPPDATA%\Programs\Cryogram` on Windows, and asks whether to add a shortcut to Launchpad or the Start menu. Once installed, you can also start it by typing `cryogram` in a new terminal.

If you prefer to download it yourself, choose **Source code (zip)** on the [latest release](https://github.com/krokodillus/cryogram/releases/latest), unzip it, and open a terminal in the extracted folder: **New Terminal at Folder** on a Mac or **Open in Terminal** on Windows. Run `sh install.sh` on a Mac, or `powershell -ExecutionPolicy Bypass -File .\install.ps1` in PowerShell on Windows. The script copies the app into the same installation folder, so it does not stay in Downloads.

You can also clone the repository and run `Start Cryogram.command` on a Mac or `start.bat` on Windows directly from your checkout.

## What you need to get started

The AI agent runs on Claude Code or Codex, so you need one of them installed on your computer, plus a subscription or an API key. The install script will show you where you can download them from if you haven't.

The workflows themselves run on Python. The install script will check for Python 3.10 or newer when you run it, and offer to download a copy into the Cryogram folder (about 40 MB) if you don't have it.

## How Cryogram works

**Build a workflow.** Describe what you want it to do, where the information comes from and what you want at the end. Cryogram explores what is needed, asks for missing details and proposes a plan. Once agreed, it builds and tests every step, asking for permission where needed.

**Run it.** Choose **Run** and provide any per-run information needed. It will pause for approval before a step sends or writes to another app, unless you have turned approval off for that step, or if it needs help with access or permissions in a browser window. **Run history** shows the results of every run, and if applicable where it paused or stopped.

**Set variables and reuse them.** Edit a workflow's saved variables on the **Inputs** tab. Create an environment under **Environments** to reuse folders, access keys and other variables across workflows. Tell the AI agent if a value should be chosen at the start of each run instead.

**Work with files and websites.** Workflows can use files on your computer and open a browser to read and interact with websites. Sign in when asked; each workflow remembers its own browser login for later runs.

**Connect to external tools and third-party apps.** Connect through APIs, databases or other interfaces to the systems you need to work with. Integrations are not pre-built: Cryogram uses official documentation, skills and its own learnings to set up consistent connections. It tests and checks all output, helping it catch problems and fix the connection if anything changes.

**Change or fix it.** Describe changes in the workflow's chat and review **Here's what will change** before approving them. If a run stops with a problem, open **Issues** and choose **Investigate**; once a fix is ready, **Continue** resumes the run. If a send was not confirmed, check whether it arrived before repeating it. All changes are tracked in the workflow's **Version history**, so you can always revert back if needed.

**Choose your AI.** Configure the builder under **Admin > Builder Agent**, and AI steps separately under **Admin > Workflow AI providers**. Workflow AI steps cannot use your subscription: hosted providers need an API key, while local providers do not. You can use the OpenAI, Anthropic and Gemini presets, or add other compatible providers and separate keys.

**Find instructions and get help.** **Information** contains the workflow's purpose, instructions and version history. **Knowledge** explains the controls in more detail, and **Help & feedback** opens the support page. If a failed run offers a problem report, you can review it before deciding whether to send it.

## How Cryogram uses your data

Your workflows and settings are stored in your computer's application data area, separate from where Cryogram is installed, so deleting or updating Cryogram does not cause you to lose anything.

Anything the AI agent uses when building a workflow is sent to your AI provider, like any other project you use it for. Once the workflow has been built, it does not see data from later runs unless you share it, for example when asking it to fix a problem. If the workflow includes AI steps, those steps send their inputs to the provider you have chosen when they run.

Secrets such as API keys are stored encrypted, so the AI agent can see their names, but never their values; the workflow uses them when connecting to the services they are for. When asking you to provide secrets, it will provide a box for you to enter them into rather than a chat - this means it can never see the values.

Cryogram always asks for permission before connecting to anything external for the first time or looking in a folder you haven't shared with it yet, and again before sending data. However, certain things can be ambiguous and the line between reading and sending can't be guaranteed in every case. For example, opening a page is meant only to read it, yet the site can still record the visit.

## Updates

Cryogram shows a note in the sidebar when a newer release is out. To update, run the install script again: it replaces the app folder, and keeps the private Python and environment it already set up, and never touches your workflows. If you cloned the repository to install, run `git pull` in the folder and start Cryogram again.

## Licence

GNU AGPL v3 - see LICENSE. You may use, fork, modify and share Cryogram freely; if you distribute it, or offer it to others as a network service, you must make your complete corresponding source available under the same licence.
