"""Dispatch a verified release snapshot to a repository with the builder workflow."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def repository(repo):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Use an explicit GitHub OWNER/REPO")
    return "https://github.com/" + repo + ".git"


def initialize(root, repo):
    """Create a public builder control repo, or check an existing installation."""
    remote = repository(repo)
    lookup = subprocess.run(["gh","api","repos/"+repo], text=True, capture_output=True)
    if lookup.returncode:
        if "HTTP 404" not in lookup.stderr:
            raise RuntimeError(lookup.stderr.strip())
        subprocess.run(["gh","repo","create",repo,"--public","--description",
                        "Native build runners for Hélène release candidates"],check=True)
    workflows = ("helene-release-kit.yml", "server-arm-native.yml")
    if subprocess.check_output(["git","ls-remote","--heads",remote]).strip():
        for name in workflows:
            state = subprocess.check_output(["gh","api",f"repos/{repo}/actions/workflows/{name}",
                                             "--jq",".state"],text=True).strip()
            if state != "active":
                raise ValueError("Enable the builder workflow: " + name)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            dest = work / ".github/workflows"
            dest.mkdir(parents=True)
            for name in workflows:
                shutil.copyfile(root/".github/workflows"/name,dest/name)
            (work/"README.md").write_text(
                f"# Hélène builders\n\nRun `python release/kit.py ci --repo {repo}` from the release source.\n",
                encoding="utf8")
            def git(*args):
                subprocess.run(["git","-C",tmp,*args],check=True)
            git("init","-q","-b","main")
            git("-c","core.autocrlf=false","add","--",".github","README.md")
            git("-c","user.name=Helene CI","-c","user.email=ci@localhost","-c","commit.gpgsign=false",
                "commit","-qm","Initialize Hélène platform builders")
            git("push",remote,"main:main")
    print("https://github.com/" + repo + "/actions")


def dispatch(root, source, commit, repo, target):
    remote = repository(repo)
    workflow = "helene-release-kit.yml"
    branch = "helene-ci/" + commit
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        chunks = work / "source-parts"
        chunks.mkdir()
        sha = hashlib.sha256()
        with source.open("rb") as stream:
            for index, data in enumerate(iter(lambda: stream.read(16*1024*1024), b"")):
                sha.update(data)
                (chunks/f"{index:06d}").write_bytes(data)
        (work / "candidate.json").write_bytes(json.dumps(
            {"commit": commit, "sha256": sha.hexdigest()}, sort_keys=True).encode())
        path = work / ".github/workflows" / workflow
        path.parent.mkdir(parents=True)
        shutil.copyfile(root / ".github/workflows" / workflow, path)
        shutil.copyfile(root / "release/ci_worker.py", work / "ci_worker.py")
        def git(*args, **kw):
            return subprocess.check_output(["git", "-C", str(work), *args], **kw).decode().strip()
        git("init", "-q", "-b", branch)
        git("-c", "core.autocrlf=false", "add", "--", ".github", "source-parts", "candidate.json", "ci_worker.py")
        # One immutable input branch per product commit. Retry without overwriting it.
        env = dict(os.environ, GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
                   GIT_AUTHOR_NAME="Helene CI", GIT_COMMITTER_NAME="Helene CI",
                   GIT_AUTHOR_EMAIL="ci@localhost", GIT_COMMITTER_EMAIL="ci@localhost")
        git("-c","commit.gpgsign=false","commit","-qm","Build release source " + commit,env=env)
        previous = git("ls-remote", remote, "refs/heads/" + branch)
        if previous and previous.split()[0] != git("rev-parse","HEAD"):
            raise ValueError("The runner already has different bytes for this commit")
        if not previous:
            git("push", remote, "HEAD:refs/heads/" + branch)
    subprocess.run(["gh","workflow","run",workflow,"--repo",repo,"--ref",branch,"-f","target="+target],check=True)
    print("https://github.com/" + repo + "/actions/workflows/" + workflow)
