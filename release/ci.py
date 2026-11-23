"""Dispatch a verified release snapshot to a repository with the builder workflow."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def dispatch(root, source, commit, repo, target):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Use an explicit GitHub OWNER/REPO")
    workflow = "helene-release-kit.yml"
    branch = "helene-ci/" + commit
    remote = "https://github.com/" + repo + ".git"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copyfile(source, work / "source.zip")
        with source.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        (work / "candidate.json").write_text(json.dumps({"commit": commit, "sha256": sha},
                                                      sort_keys=True), encoding="utf8")
        path = work / ".github/workflows" / workflow
        path.parent.mkdir(parents=True)
        shutil.copyfile(root / ".github/workflows" / workflow, path)
        shutil.copyfile(root / "release/ci_worker.py", work / "ci_worker.py")
        def git(*args, **kw):
            return subprocess.check_output(["git", "-C", str(work), *args], **kw).decode().strip()
        git("init", "-q", "-b", branch)
        git("-c", "core.autocrlf=false", "add", "--", ".github", "source.zip", "candidate.json", "ci_worker.py")
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
