# Each workflow installs its own packages into its own bundle venv - nothing is installed system-wide
from __future__ import annotations

import re
import subprocess
import sys

import config

DENYLIST = {
    "anthropic", "openai", "litellm", "google-genai", "google-generativeai",
    "mistralai", "cohere", "ollama", "groq", "together", "replicate",
    "langchain", "langchain-openai", "langchain-anthropic", "llama-index",
    "claude-agent-sdk", "boto3-bedrock", "vertexai",
}

APP_PACKAGES = {"playwright"}

# The packages a bundle venv installs itself: everything declared except the app's own
def bundle_only(packages: list) -> list[str]:
    return [str(p) for p in (packages or []) if str(p).strip()
            and base_name(str(p)) not in APP_PACKAGES]

_SPEC = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(==[A-Za-z0-9.*+!_-]+)?$")

def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())

def base_name(spec: str) -> str:
    return _norm(spec.split("==")[0])

# Plain names or exact pins only - no URLs, git refs or local paths
def validate(packages: list) -> list[str]:
    errors = []
    for p in packages or []:
        p = str(p).strip()
        if not _SPEC.match(p):
            errors.append(f"{p!r}: only 'name' or 'name==version' is allowed - no "
                          "URLs, git refs or paths")
            continue
        if base_name(p) in DENYLIST:
            errors.append(f"{p!r}: refused - it duplicates a harness feature. All "
                          "intelligence goes through ai_call (the only path with a key); never install an AI SDK or model client")
    return errors

def venv_dir(workflow_id: str):
    return config.workflow_dir(workflow_id) / "venv"

# The bundle venv's interpreter when there is one, else the app's own
def python_for(workflow_id: str) -> str:
    py = venv_dir(workflow_id) / "bin" / "python"
    if py.exists():
        return str(py)
    py = venv_dir(workflow_id) / "Scripts" / "python.exe"
    return str(py) if py.exists() else sys.executable

VENV_TIMEOUT = 600

# A warm venv template copied per bundle, instead of a slow venv-plus-ensurepip each time
def _template_venv():
    import shutil
    tdir = config.DATA_DIR / "venv_template"
    marker = tdir / f".py{sys.version_info.major}.{sys.version_info.minor}"
    if not marker.exists():
        if tdir.exists():
            shutil.rmtree(tdir)
        tdir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(tdir)],
                       capture_output=True, text=True, timeout=VENV_TIMEOUT,
                       check=True)
        marker.touch()
    return tdir

# Which declared packages are not yet installed in the workflow's own venv
def unsatisfied(workflow_id: str, packages: list[str]) -> list[str]:
    packages = bundle_only(packages)
    if not packages:
        return []
    vdir = venv_dir(workflow_id)
    if not (vdir / "bin").exists() and not (vdir / "Scripts").exists():
        return packages
    try:
        py = python_for(workflow_id)
        frozen = subprocess.run([py, "-m", "pip", "freeze"],
                                capture_output=True, text=True, timeout=600)
        have = {base_name(ln): ln.strip() for ln in frozen.stdout.splitlines()
                if "==" in ln}
        def ok(spec: str) -> bool:
            b = base_name(spec)
            return b in have and (("==" not in spec)
                                  or _norm(have[b]) == _norm(spec))
        return [p for p in packages if not ok(p)]
    except Exception:
        return packages

# Which of these import names the workflow's own environment cannot provide, asked of that environment in one call
def missing_modules(workflow_id: str, roots: list[str],
                    app_packages: bool = False) -> list[str]:
    roots = [str(r) for r in (roots or []) if str(r).strip()]
    if not roots:
        return []
    try:
        py = python_for(workflow_id)
        extra = config.app_site_packages() if app_packages else ""
        probe = ("import importlib.util, sys\n"
                 f"sys.path.append({extra!r}) if {extra!r} else None\n"
                 "print('\\n'.join(m for m in sys.argv[1:] if importlib.util.find_spec(m) is None))")
        out = subprocess.run([py, "-c", probe, *roots], capture_output=True,
                             text=True, timeout=60)
        if out.returncode != 0:
            return roots
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return roots

# Installs a workflow's packages into its own venv and pins the versions used
def install(workflow_id: str, packages: list[str], timeout: int = 600) -> dict:
    import shutil
    import time as _time
    t0 = _time.time()

    packages = bundle_only(packages)
    if not packages:
        return {"ok": True, "pins": {}, "seconds": 0.0}
    problems = validate(packages)
    if problems:
        return {"error": "; ".join(problems)}
    vdir = venv_dir(workflow_id)
    try:
        if not (vdir / "bin").exists() and not (vdir / "Scripts").exists():
            vdir.parent.mkdir(parents=True, exist_ok=True)
            try:
                try:
                    shutil.copytree(_template_venv(), vdir, symlinks=True,
                                    ignore=shutil.ignore_patterns(".py*"))
                except subprocess.TimeoutExpired:
                    raise
                except Exception:
                    if vdir.exists():
                        shutil.rmtree(vdir)
                    subprocess.run([sys.executable, "-m", "venv", str(vdir)],
                                   capture_output=True, text=True,
                                   timeout=VENV_TIMEOUT, check=True)
            except subprocess.TimeoutExpired:
                return {"error": "Setting up the workflow's packages took "
                                 f"longer than {VENV_TIMEOUT} seconds, the most "
                                 "it waits, often because of a slow disk or antivirus. Try again."}
        py = python_for(workflow_id)

        frozen0 = subprocess.run([py, "-m", "pip", "freeze"],
                                 capture_output=True, text=True, timeout=600)
        have = {base_name(ln): ln.strip() for ln in frozen0.stdout.splitlines()
                if "==" in ln}
        def satisfied(spec: str) -> bool:
            b = base_name(spec)
            if b not in have:
                return False
            return ("==" not in spec) or _norm(have[b]) == _norm(spec)
        if packages and all(satisfied(p) for p in packages):
            wanted0 = {base_name(p) for p in packages}
            return {"ok": True, "already_installed": True,
                    "seconds": round(_time.time() - t0, 1),
                    "pins": sorted(v for k, v in have.items() if k in wanted0)}
        r = subprocess.run([py, "-m", "pip", "install", "--quiet",
                            "--prefer-binary", *packages],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"error": f"install failed: {(r.stderr or r.stdout)[-600:]}"}
        frozen = subprocess.run([py, "-m", "pip", "freeze"],
                                capture_output=True, text=True, timeout=600)
        wanted = {base_name(p) for p in packages}
        pins = [ln.strip() for ln in frozen.stdout.splitlines()
                if "==" in ln and base_name(ln) in wanted]
        return {"ok": True, "pins": sorted(pins),
                "seconds": round(_time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        return {"error": f"Installing packages took longer than {timeout} "
                         "seconds, the most it waits. Try again."}
    except subprocess.CalledProcessError as e:
        return {"error": f"could not create the workflow's environment: "
                         f"{(e.stderr or '')[-400:]}"}
