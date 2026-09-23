#!/usr/bin/env python3
"""Narrow Prometheus proposal transport. Imports no destination code.

Only run after credential-free validation. Does not request merge/certification.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

REPO = "yellowcooln/openhop-plugin-catalogue"
UPSTREAM = "openhop-dev/openhop-plugin-catalogue"
BASE = "/repos/" + REPO
UPSTREAM_BASE = "/repos/" + UPSTREAM


def require(condition, message):
    if not condition:
        raise ValueError(message)


def push_args(branch, observed_sha):
    require(re.fullmatch(r"automation/openhop-prometheus-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", branch), "invalid branch")
    require(observed_sha == "" or re.fullmatch(r"[0-9a-f]{40}", observed_sha), "invalid lease")
    return ["push", f"--force-with-lease=refs/heads/{branch}:{observed_sha}",
            "https://github.com/" + REPO + ".git", f"HEAD:refs/heads/{branch}"]


def publish(api, push, receipt):
    if not receipt["changed"]:
        return
    branch = receipt["branch"]
    push_args(branch, receipt["remote_sha"])
    require(api(UPSTREAM_BASE + "/git/ref/heads/main")["object"]["sha"] == receipt["base_sha"],
            "catalogue main advanced; retry preparation")
    prs = api(UPSTREAM_BASE + f"/pulls?state=open&head=yellowcooln:{branch}&base=main&per_page=100")
    require(len(prs) <= 1, "duplicate open proposal PRs")
    existing = prs[0] if prs else None
    if existing:
        require(existing["head"]["ref"] == branch and existing["head"]["repo"]["full_name"] == REPO
                and existing["base"]["ref"] == "main" and existing["base"]["repo"]["full_name"] == UPSTREAM
                and existing["state"] == "open", "unexpected PR target")
    head = push(receipt)
    fields = receipt["fields"]
    title = f"Approve Prometheus {fields['version']}"
    body = (f"## Prometheus release\n\n- Plugin: `openhop.prometheus`\n- Version: `{fields['version']}`\n"
            f"- Source and public release tag SHA: `{fields['source_revision']}`\n"
            f"- Wheel: {fields['wheel_url']}\n- SHA-256: `{fields['sha256']}`\n"
            f"- Originating release run: {receipt['origin_run_url']}\n"
            "- Minimum Repeater version: `0.3.0` (initial proposal; preserved thereafter)\n\n"
            "Public wheel verified with the trusted catalogue Python service profile; "
            "source/tag, RECORD, manifest and catalogue validation/tests passed without a write token. "
            "Initial registration requires human approval; subsequent proposals change only four "
            "release fields and preserve compatibility and branding. "
            "Catalogue-owned policy and required checks decide automatic merging. "
            "This producer neither certifies itself nor requests auto-merge.\n")
    if existing:
        number = existing["number"]
        api(UPSTREAM_BASE + f"/pulls/{number}", "PATCH", dict(title=title, body=body))
        draft = existing["draft"]
    else:
        result = api(UPSTREAM_BASE + "/pulls", "POST", dict(title=title, body=body, head="yellowcooln:" + branch,
                     base="main", draft=receipt["initial"], maintainer_can_modify=False))
        number = result["number"]
        draft = receipt["initial"]
    live = api(UPSTREAM_BASE + f"/pulls/{number}")
    require(live["head"]["sha"] == head and live["head"]["ref"] == branch
            and live["head"]["repo"]["full_name"] == REPO
            and live["base"]["ref"] == "main" and live["base"]["repo"]["full_name"] == UPSTREAM
            and live["state"] == "open"
            and live["draft"] == draft and live["body"] == body and live["title"] == title,
            "PR readback mismatch (may have concurrently merged; inspect before retry)")
    print(f"Verified catalogue PR https://github.com/{UPSTREAM}/pull/{number}")


class Transport:
    def __init__(self, fork_token, upstream_token):
        require(fork_token and upstream_token, "installation token missing")
        self.fork_token = fork_token
        self.upstream_token = upstream_token

    def __call__(self, path, method="GET", data=None):
        require(path.startswith(BASE + "/") or path.startswith(UPSTREAM_BASE + "/")
                or path == "/users/openhop-catalogue-publisher[bot]",
                "unexpected write transport destination")
        require(method == "GET" or (method in {"POST", "PATCH"} and path.startswith(UPSTREAM_BASE + "/pulls")),
                "unexpected API write destination")
        token = self.upstream_token if path.startswith(UPSTREAM_BASE + "/") else self.fork_token
        request = urllib.request.Request("https://api.github.com" + path,
            data=json.dumps(data).encode() if data is not None else None, method=method,
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "prometheus-catalogue-publisher"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def push_candidate(root, candidate, api, receipt, token):
    """Use plumbing, a private index, no hooks/filters or destination execution."""
    require(hashlib.sha256(candidate).hexdigest() == receipt["candidate_sha256"], "candidate changed")
    user = api("/users/openhop-catalogue-publisher[bot]")
    require(user["id"] == 325431437 and user["login"] == "openhop-catalogue-publisher[bot]",
            "unexpected App bot identity")
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME=user["login"], GIT_COMMITTER_NAME=user["login"],
               GIT_AUTHOR_EMAIL=f"{user['id']}+{user['login']}@users.noreply.github.com",
               GIT_COMMITTER_EMAIL=f"{user['id']}+{user['login']}@users.noreply.github.com")
    # Credentials go only to git's HTTPS header, never a persisted remote or argv.
    auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env.update(GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + auth,
               GIT_CONFIG_KEY_1="credential.helper", GIT_CONFIG_VALUE_1="",
               GIT_CONFIG_KEY_2="core.hooksPath", GIT_CONFIG_VALUE_2="/dev/null")
    def git(*args, data=None):
        p = subprocess.run(["git", "-C", str(root), *args], input=data,
                           capture_output=True, env=env)
        require(p.returncode == 0, "git operation failed; inspect branch/lease before retry")
        return p.stdout.decode().strip()
    require(git("rev-parse", "HEAD") == receipt["base_sha"], "local base changed")
    with tempfile.TemporaryDirectory() as temp:
        env["GIT_INDEX_FILE"] = str(Path(temp) / "index")
        git("read-tree", receipt["base_sha"])
        blob = git("hash-object", "-w", "--stdin", data=candidate)
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},catalogue.json")
        tree = git("write-tree")
        commit = git("commit-tree", tree, "-p", receipt["base_sha"],
                     data=f"Propose Prometheus {receipt['fields']['version']}\n".encode())
        args = push_args(receipt["branch"], receipt["remote_sha"])
        args[-1] = f"{commit}:refs/heads/{receipt['branch']}"
        git(*args)
    live = api(BASE + "/git/ref/heads/" + receipt["branch"])
    require(live["object"]["sha"] == commit, "branch readback mismatch")
    content = api(BASE + f"/contents/catalogue.json?ref={commit}")
    require(base64.b64decode(content["content"]) == candidate, "remote candidate bytes mismatch")
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads((args.proposal / "proposal.json").read_text())
    if not receipt["changed"]:
        return
    require(os.environ.get("APP_SLUG") == "openhop-catalogue-publisher", "unexpected App")
    token = os.environ.get("FORK_TOKEN", "")
    api = Transport(token, os.environ.get("UPSTREAM_PR_TOKEN", ""))
    candidate = (args.proposal / "catalogue.json").read_bytes()
    publish(api, lambda r: push_candidate(args.catalogue, candidate, api, r, token), receipt)


if __name__ == "__main__":
    main()
