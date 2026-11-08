"""Read-only, zero-model repository snapshot loop.

The loop never calls a model and never writes to a repository. It records the
branch, HEAD, working-tree status and last commit for each configured repo, and
writes a short local report only when that snapshot changes or a repo is
unreadable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess

GIT_TIMEOUT_SECONDS = 8
REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RepoSnapshot:
    path: str
    branch: str
    head: str
    status: tuple[str, ...]
    last_subject: str
    ahead: int | None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    def digest(self) -> str:
        payload = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(message[0] if message else f"git exited {result.returncode}")
    return result.stdout.strip()


def snapshot_repo(path: str | Path) -> RepoSnapshot:
    repo = Path(path)
    try:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        head = _git(repo, "rev-parse", "HEAD")
        status = tuple(line for line in _git(repo, "status", "--porcelain=v1").splitlines() if line)
        subject = _git(repo, "log", "-1", "--format=%s")
        try:
            ahead = int(_git(repo, "rev-list", "--count", "@{u}..HEAD"))
        except RuntimeError:
            ahead = None
        return RepoSnapshot(
            path=str(repo.resolve()),
            branch=branch,
            head=head,
            status=status,
            last_subject=subject,
            ahead=ahead,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return RepoSnapshot(
            path=str(repo.resolve()),
            branch="unknown",
            head="unknown",
            status=(),
            last_subject="",
            ahead=None,
            error=str(error),
        )


def load_state(path: str | Path) -> dict[str, str]:
    state_path = Path(path)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    repos = data.get("repos") if isinstance(data, dict) else None
    return {str(key): str(value) for key, value in repos.items()} if isinstance(repos, dict) else {}


def save_state(path: str | Path, snapshots: list[RepoSnapshot]) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "repos": {snapshot.path: snapshot.digest() for snapshot in snapshots},
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def changed_paths(previous: dict[str, str], snapshots: list[RepoSnapshot]) -> list[str]:
    changed: list[str] = []
    current = {snapshot.path: snapshot.digest() for snapshot in snapshots}
    for path in sorted(set(previous) | set(current)):
        if previous.get(path) != current.get(path):
            changed.append(path)
    return changed


def render_report(snapshots: list[RepoSnapshot], changed: list[str], generated_at: str) -> str:
    lines = [
        "# Local loop report",
        "",
        f"Generated: {generated_at}",
        f"Changed: {', '.join(changed) if changed else 'none (manual run)'}",
        "",
        "| Repository | Branch | HEAD | Status | Ahead | Last commit |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for snapshot in snapshots:
        status = f"{len(snapshot.status)} changed" if snapshot.status else "clean"
        ahead = "n/a" if snapshot.ahead is None else str(snapshot.ahead)
        head = snapshot.head[:12] if snapshot.head != "unknown" else "unknown"
        lines.append(
            f"| {snapshot.path} | {snapshot.branch} | {head} | {status} | {ahead} | {snapshot.last_subject} |"
        )
        if snapshot.error:
            lines.append(f"|  |  |  | error |  | {snapshot.error} |")
    lines.extend(
        [
            "",
            "Read-only check: no AI call, no network, no repository write.",
            "Next: if changed, inspect the diff and update docs/ai-task-ledger.md; if stable, do nothing.",
        ]
    )
    return "\n".join(lines) + "\n"


def evaluate(
    snapshots: list[RepoSnapshot],
    previous: dict[str, str],
    *,
    force: bool = False,
) -> tuple[bool, list[str]]:
    changed = changed_paths(previous, snapshots)
    errors = any(snapshot.error for snapshot in snapshots)
    return bool(force or changed or errors), changed


def write_report(
    report_dir: str | Path,
    snapshots: list[RepoSnapshot],
    changed: list[str],
) -> tuple[Path, Path]:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    markdown_path = directory / f"local-loop-{stamp}.md"
    json_path = directory / f"local-loop-{stamp}.json"
    generated_at = datetime.now(UTC).isoformat()
    markdown_path.write_text(render_report(snapshots, changed, generated_at), encoding="utf-8")
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "changed": changed,
        "repos": [snapshot.as_dict() for snapshot in snapshots],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return markdown_path, json_path


def run_once(
    repos: list[str | Path],
    state_path: str | Path,
    report_dir: str | Path,
    *,
    force: bool = False,
) -> dict:
    snapshots = [snapshot_repo(repo) for repo in repos]
    should_report, changed = evaluate(snapshots, load_state(state_path), force=force)
    if not should_report:
        return {"reported": False, "changed": [], "errors": []}
    markdown_path, json_path = write_report(report_dir, snapshots, changed)
    save_state(state_path, snapshots)
    return {
        "reported": True,
        "changed": changed,
        "errors": [snapshot.error for snapshot in snapshots if snapshot.error],
        "markdown": str(markdown_path),
        "json": str(json_path),
    }
