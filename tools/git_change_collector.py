"""Collect Git change metadata without reading or returning diff bodies."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple


LOGGER = logging.getLogger("collector")
VALIDATION_TIMEOUT_SECONDS = 10
COLLECTION_TIMEOUT_SECONDS = 30

_COMMIT_HASH = re.compile(r"^[0-9a-fA-F]{40,64}$")
_CONFLICT_PAIRS = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})
_STATUS_VALUES = {
    " ": "none",
    "M": "modified",
    "T": "type_changed",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "conflicted",
    "?": "untracked",
}


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class GitCommandRunner:
    """Run Git safely with argument lists and bounded execution time."""

    def __init__(self, executable: Optional[str] = None) -> None:
        self._executable = executable
        self._availability_result: Optional[GitCommandResult] = None

    def check_available(self, timeout_seconds: int) -> GitCommandResult:
        if self._availability_result is not None:
            return self._availability_result
        executable = self._executable or shutil.which("git")
        if executable is None:
            raise FileNotFoundError("git_not_found")
        self._executable = executable
        result = self._run([executable, "--version"], None, timeout_seconds)
        self._availability_result = result
        return result

    def run(
        self, workspace: Path, arguments: Sequence[str], timeout_seconds: int
    ) -> GitCommandResult:
        executable = self._executable or shutil.which("git")
        if executable is None:
            raise FileNotFoundError("git_not_found")
        self._executable = executable
        command = [executable, "-C", str(workspace), *arguments]
        return self._run(command, workspace, timeout_seconds)

    @staticmethod
    def _run(
        command: Sequence[str], cwd: Optional[Path], timeout_seconds: int
    ) -> GitCommandResult:
        environment = os.environ.copy()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout_seconds,
            env=environment,
        )
        return GitCommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


@dataclass(frozen=True)
class GitNumstat:
    state: str
    added: Optional[int]
    deleted: Optional[int]


@dataclass(frozen=True)
class GitFileStatus:
    index: str
    worktree: str
    committed_in_session: str


@dataclass(frozen=True)
class GitFileChange:
    change_id: str
    path: str
    old_path: Optional[str]
    status: GitFileStatus
    staged: GitNumstat
    unstaged: GitNumstat
    committed_in_session: GitNumstat
    binary: Optional[bool]
    scopes: Tuple[str, ...]


@dataclass(frozen=True)
class GitCollectionIssue:
    kind: str
    command: str
    retryable: bool


@dataclass(frozen=True)
class GitRepositoryState:
    collection_status: str
    root_name: Optional[str]
    branch: Optional[str]
    head: Optional[str]
    session_start_commit: Optional[str]
    session_start_source: Optional[str]
    clean: bool


@dataclass(frozen=True)
class GitCollectionResult:
    repository: GitRepositoryState
    files: Tuple[GitFileChange, ...]
    issues: Tuple[GitCollectionIssue, ...]
    retry_required: bool


@dataclass(frozen=True)
class _StatusEntry:
    path: str
    old_path: Optional[str]
    index: str
    worktree: str


@dataclass(frozen=True)
class _NameStatusEntry:
    path: str
    old_path: Optional[str]
    status: str


@dataclass(frozen=True)
class _NumstatEntry:
    path: str
    old_path: Optional[str]
    value: GitNumstat


_DEFAULT_RUNNER = GitCommandRunner()


def collect_git_changes(
    workspace: Path,
    workspace_id: str,
    *,
    jsonl_session_start_commit: Optional[str] = None,
    first_observed_head: Optional[str] = None,
    runner: Optional[GitCommandRunner] = None,
) -> GitCollectionResult:
    """Collect current and committed change metadata for one Git workspace."""

    root = workspace.resolve(strict=False)
    command_runner = runner or _DEFAULT_RUNNER
    issues: List[GitCollectionIssue] = []

    available = _run_available(command_runner, issues)
    if available is None:
        return _failed_result(root.name or None, issues)

    inside = _run(command_runner, root, ("rev-parse", "--is-inside-work-tree"), "validate_repository", VALIDATION_TIMEOUT_SECONDS, issues)
    if inside is None or inside.stdout.strip() != b"true":
        if inside is not None:
            _add_issue(issues, "not_git_repository", "validate_repository", False)
        return _failed_result(root.name or None, issues)

    top_level = _run(command_runner, root, ("rev-parse", "--show-toplevel"), "resolve_root", VALIDATION_TIMEOUT_SECONDS, issues)
    if top_level is None:
        return _failed_result(root.name or None, issues)
    decoded_root = _decode_text(top_level.stdout.strip(), "resolve_root", issues)
    if decoded_root is None or not _same_path(Path(decoded_root), root):
        _add_issue(issues, "workspace_is_not_repository_root", "resolve_root", False)
        return _failed_result(root.name or None, issues)

    head_result = _run(command_runner, root, ("rev-parse", "--verify", "HEAD"), "read_head", VALIDATION_TIMEOUT_SECONDS, issues)
    if head_result is None:
        return _failed_result(root.name or None, issues)
    head = _decode_commit(head_result.stdout, "read_head", issues)
    if head is None:
        return _failed_result(root.name or None, issues)

    branch = _read_branch(command_runner, root, issues)
    status_result = _run(command_runner, root, ("-c", "status.renames=copies", "status", "--porcelain=v1", "-z", "--untracked-files=all"), "status", COLLECTION_TIMEOUT_SECONDS, issues)
    if status_result is None:
        return _failed_result(root.name or None, issues, head=head, branch=branch)
    try:
        status_entries = parse_porcelain_status(status_result.stdout)
    except ValueError:
        _add_issue(issues, "invalid_git_output", "status", True)
        return _failed_result(root.name or None, issues, head=head, branch=branch)

    changes: Dict[str, GitFileChange] = {}
    for entry in status_entries:
        key = _path_key(entry.path)
        scopes = _current_scopes(entry)
        changes[key] = GitFileChange(
            change_id=_change_id(workspace_id, entry.path),
            path=entry.path,
            old_path=entry.old_path,
            status=GitFileStatus(entry.index, entry.worktree, "none"),
            staged=_pending_numstat("staged", scopes, entry),
            unstaged=_pending_numstat("worktree", scopes, entry),
            committed_in_session=_not_applicable(),
            binary=None,
            scopes=scopes,
        )

    _apply_current_numstat(command_runner, root, changes, "staged", issues)
    _apply_current_numstat(command_runner, root, changes, "unstaged", issues)

    session_start, start_source = _select_session_start(
        jsonl_session_start_commit, first_observed_head, head
    )
    if session_start is not None:
        valid_start = _validate_session_start(
            command_runner, root, session_start, head, issues
        )
        if valid_start and session_start != head:
            _apply_committed_changes(
                command_runner,
                root,
                workspace_id,
                changes,
                session_start,
                issues,
            )
        elif not _COMMIT_HASH.fullmatch(session_start):
            session_start = None
            start_source = None

    files = tuple(
        _finalize_binary(change)
        for change in sorted(changes.values(), key=lambda item: item.path.casefold())
    )
    collection_status = "warning" if issues else "ok"
    retry_required = any(issue.retryable for issue in issues)
    repository = GitRepositoryState(
        collection_status=collection_status,
        root_name=root.name or None,
        branch=branch,
        head=head,
        session_start_commit=session_start,
        session_start_source=start_source,
        clean=not status_entries,
    )
    LOGGER.info(
        "Git変更を収集: workspace_id=%s status=%s files=%d warnings=%d",
        workspace_id,
        collection_status,
        len(files),
        len(issues),
    )
    return GitCollectionResult(repository, files, tuple(issues), retry_required)


def parse_porcelain_status(data: bytes) -> Tuple[_StatusEntry, ...]:
    """Parse `git status --porcelain=v1 -z` without decoding diff content."""

    fields = data.split(b"\0")
    entries: List[_StatusEntry] = []
    index = 0
    while index < len(fields) - 1:
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4 or field[2:3] != b" ":
            raise ValueError("invalid_porcelain_record")
        pair = field[:2].decode("ascii", errors="strict")
        path = _decode_path(field[3:])
        old_path: Optional[str] = None
        if pair[0] in "RC" or pair[1] in "RC":
            if index >= len(fields) - 1:
                raise ValueError("missing_rename_source")
            old_path = _decode_path(fields[index])
            index += 1
        index_status, worktree_status = _map_status_pair(pair)
        entries.append(_StatusEntry(path, old_path, index_status, worktree_status))
    return tuple(entries)


def parse_name_status(data: bytes) -> Tuple[_NameStatusEntry, ...]:
    """Parse NUL-separated `git diff --name-status` output."""

    fields = data.split(b"\0")
    entries: List[_NameStatusEntry] = []
    index = 0
    while index < len(fields) - 1:
        token = fields[index]
        index += 1
        if not token:
            continue
        if b"\t" in token:
            status_bytes, path_bytes = token.split(b"\t", 1)
        else:
            status_bytes = token
            if index >= len(fields) - 1:
                raise ValueError("missing_name_status_path")
            path_bytes = fields[index]
            index += 1
        status_code = status_bytes.decode("ascii", errors="strict")
        status = _map_diff_status(status_code)
        old_path: Optional[str] = None
        path = _decode_path(path_bytes)
        if status_code[:1] in ("R", "C"):
            old_path = path
            if index >= len(fields) - 1:
                raise ValueError("missing_rename_target")
            path = _decode_path(fields[index])
            index += 1
        entries.append(_NameStatusEntry(path, old_path, status))
    return tuple(entries)


def parse_numstat(data: bytes) -> Tuple[_NumstatEntry, ...]:
    """Parse NUL-separated numstat output, including rename records."""

    entries: List[_NumstatEntry] = []
    offset = 0
    while offset < len(data):
        first_tab = data.find(b"\t", offset)
        second_tab = data.find(b"\t", first_tab + 1)
        terminator = data.find(b"\0", second_tab + 1)
        if first_tab < 0 or second_tab < 0 or terminator < 0:
            if data[offset:] == b"":
                break
            raise ValueError("invalid_numstat_record")
        added_raw = data[offset:first_tab]
        deleted_raw = data[first_tab + 1 : second_tab]
        path_raw = data[second_tab + 1 : terminator]
        offset = terminator + 1
        old_path: Optional[str] = None
        if path_raw == b"":
            old_end = data.find(b"\0", offset)
            new_end = data.find(b"\0", old_end + 1)
            if old_end < 0 or new_end < 0:
                raise ValueError("invalid_numstat_rename")
            old_path = _decode_path(data[offset:old_end])
            path = _decode_path(data[old_end + 1 : new_end])
            offset = new_end + 1
        else:
            path = _decode_path(path_raw)
        entries.append(
            _NumstatEntry(path, old_path, _numstat_value(added_raw, deleted_raw))
        )
    return tuple(entries)


def _apply_current_numstat(
    runner: GitCommandRunner,
    root: Path,
    changes: Dict[str, GitFileChange],
    target: str,
    issues: List[GitCollectionIssue],
) -> None:
    arguments = (
        ("diff", "--cached", "--numstat", "-z", "--find-renames", "--find-copies")
        if target == "staged"
        else ("diff", "--numstat", "-z", "--find-renames", "--find-copies")
    )
    result = _run(runner, root, arguments, f"numstat_{target}", COLLECTION_TIMEOUT_SECONDS, issues)
    if result is None:
        _mark_scope_failed(changes, target)
        return
    try:
        entries = parse_numstat(result.stdout)
    except ValueError:
        _add_issue(issues, "invalid_git_output", f"numstat_{target}", True)
        _mark_scope_failed(changes, target)
        return
    for entry in entries:
        key = _path_key(entry.path)
        change = changes.get(key)
        if change is None:
            continue
        if target == "staged":
            changes[key] = replace(change, staged=entry.value)
        else:
            changes[key] = replace(change, unstaged=entry.value)
    _record_missing_numstat(changes, target, issues)


def _apply_committed_changes(
    runner: GitCommandRunner,
    root: Path,
    workspace_id: str,
    changes: Dict[str, GitFileChange],
    start: str,
    issues: List[GitCollectionIssue],
) -> None:
    revision = f"{start}..HEAD"
    names = _run(runner, root, ("diff", "--name-status", "-z", "--find-renames", "--find-copies", revision), "committed_names", COLLECTION_TIMEOUT_SECONDS, issues)
    if names is None:
        return
    try:
        name_entries = parse_name_status(names.stdout)
    except ValueError:
        _add_issue(issues, "invalid_git_output", "committed_names", True)
        return
    for entry in name_entries:
        key = _path_key(entry.path)
        existing = changes.get(key)
        if existing is None:
            existing = GitFileChange(
                change_id=_change_id(workspace_id, entry.path),
                path=entry.path,
                old_path=entry.old_path,
                status=GitFileStatus("none", "none", entry.status),
                staged=_not_applicable(),
                unstaged=_not_applicable(),
                committed_in_session=_failed_numstat(),
                binary=None,
                scopes=("committed_in_session",),
            )
        else:
            existing = replace(
                existing,
                old_path=existing.old_path or entry.old_path,
                status=replace(
                    existing.status, committed_in_session=entry.status
                ),
                scopes=_merge_scopes(existing.scopes, ("committed_in_session",)),
                committed_in_session=_failed_numstat(),
            )
        changes[key] = existing

    stats = _run(runner, root, ("diff", "--numstat", "-z", "--find-renames", "--find-copies", revision), "numstat_committed", COLLECTION_TIMEOUT_SECONDS, issues)
    if stats is None:
        return
    try:
        entries = parse_numstat(stats.stdout)
    except ValueError:
        _add_issue(issues, "invalid_git_output", "numstat_committed", True)
        return
    for entry in entries:
        key = _path_key(entry.path)
        change = changes.get(key)
        if change is not None:
            changes[key] = replace(change, committed_in_session=entry.value)
    _record_missing_numstat(changes, "committed_in_session", issues)


def _validate_session_start(
    runner: GitCommandRunner,
    root: Path,
    start: str,
    head: str,
    issues: List[GitCollectionIssue],
) -> bool:
    if not _COMMIT_HASH.fullmatch(start):
        _add_issue(issues, "invalid_session_start_commit", "validate_session_start", False)
        return False
    exists = _run(runner, root, ("rev-parse", "--verify", f"{start}^{{commit}}"), "validate_session_start", VALIDATION_TIMEOUT_SECONDS, issues)
    if exists is None:
        return False
    if start == head:
        return True
    try:
        ancestor = runner.run(
            root,
            ("merge-base", "--is-ancestor", start, "HEAD"),
            VALIDATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _add_issue(issues, "git_timeout", "validate_session_ancestry", True)
        return False
    except (OSError, ValueError):
        _add_issue(issues, "git_execution_failed", "validate_session_ancestry", True)
        return False
    if ancestor.returncode == 0:
        return True
    if ancestor.returncode == 1:
        _add_issue(issues, "session_start_not_ancestor", "validate_session_ancestry", False)
        return False
    _add_issue(issues, "git_nonzero_exit", "validate_session_ancestry", True)
    return False


def _run_available(
    runner: GitCommandRunner, issues: List[GitCollectionIssue]
) -> Optional[GitCommandResult]:
    try:
        result = runner.check_available(VALIDATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _add_issue(issues, "git_timeout", "git_version", True)
        return None
    except (FileNotFoundError, OSError, ValueError):
        _add_issue(issues, "git_not_found", "git_version", False)
        return None
    if result.returncode != 0:
        _add_issue(issues, "git_nonzero_exit", "git_version", False)
        return None
    return result


def _run(
    runner: GitCommandRunner,
    root: Path,
    arguments: Sequence[str],
    label: str,
    timeout: int,
    issues: List[GitCollectionIssue],
) -> Optional[GitCommandResult]:
    try:
        result = runner.run(root, arguments, timeout)
    except subprocess.TimeoutExpired:
        _add_issue(issues, "git_timeout", label, True)
        return None
    except (OSError, ValueError):
        _add_issue(issues, "git_execution_failed", label, True)
        return None
    if result.returncode != 0:
        _add_issue(issues, "git_nonzero_exit", label, True)
        return None
    return result


def _read_branch(
    runner: GitCommandRunner, root: Path, issues: List[GitCollectionIssue]
) -> Optional[str]:
    try:
        result = runner.run(
            root,
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            VALIDATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _add_issue(issues, "git_timeout", "read_branch", True)
        return None
    except (OSError, ValueError):
        _add_issue(issues, "git_execution_failed", "read_branch", True)
        return None
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        _add_issue(issues, "git_nonzero_exit", "read_branch", True)
        return None
    return _decode_text(result.stdout.strip(), "read_branch", issues)


def _select_session_start(
    jsonl_commit: Optional[str], first_observed: Optional[str], head: str
) -> Tuple[Optional[str], Optional[str]]:
    if jsonl_commit:
        return jsonl_commit.lower(), "jsonl"
    if first_observed:
        return first_observed.lower(), "first_observed"
    return head, "first_observed"


def _current_scopes(entry: _StatusEntry) -> Tuple[str, ...]:
    scopes: List[str] = []
    if entry.index not in ("none", "untracked"):
        scopes.append("staged")
    if entry.worktree != "none" or entry.index == "untracked":
        scopes.append("worktree")
    return tuple(scopes)


def _pending_numstat(
    scope: str, scopes: Sequence[str], entry: _StatusEntry
) -> GitNumstat:
    if scope not in scopes:
        return _not_applicable()
    if entry.index == "untracked" or entry.worktree == "untracked":
        return GitNumstat("not_inspected", None, None)
    return _failed_numstat()


def _mark_scope_failed(changes: Dict[str, GitFileChange], target: str) -> None:
    scope = "staged" if target == "staged" else "worktree"
    for key, change in tuple(changes.items()):
        if scope not in change.scopes:
            continue
        if target == "staged":
            changes[key] = replace(change, staged=_failed_numstat())
        elif change.unstaged.state != "not_inspected":
            changes[key] = replace(change, unstaged=_failed_numstat())


def _record_missing_numstat(
    changes: Dict[str, GitFileChange],
    target: str,
    issues: List[GitCollectionIssue],
) -> None:
    attribute = {
        "staged": "staged",
        "unstaged": "unstaged",
        "committed_in_session": "committed_in_session",
    }[target]
    if any(getattr(change, attribute).state == "failed" for change in changes.values()):
        _add_issue(issues, "missing_numstat", f"numstat_{target}", True)


def _finalize_binary(change: GitFileChange) -> GitFileChange:
    states = (
        change.staged.state,
        change.unstaged.state,
        change.committed_in_session.state,
    )
    if "binary" in states:
        binary: Optional[bool] = True
    elif "measured" in states:
        binary = False
    else:
        binary = None
    return replace(change, binary=binary)


def _numstat_value(added: bytes, deleted: bytes) -> GitNumstat:
    if added == b"-" and deleted == b"-":
        return GitNumstat("binary", None, None)
    try:
        added_value = int(added.decode("ascii"))
        deleted_value = int(deleted.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid_numstat_count") from error
    if added_value < 0 or deleted_value < 0:
        raise ValueError("negative_numstat_count")
    return GitNumstat("measured", added_value, deleted_value)


def _map_status_pair(pair: str) -> Tuple[str, str]:
    if pair == "??":
        return "none", "untracked"
    if pair in _CONFLICT_PAIRS or "U" in pair:
        return "conflicted", "conflicted"
    try:
        return _STATUS_VALUES[pair[0]], _STATUS_VALUES[pair[1]]
    except (KeyError, IndexError) as error:
        raise ValueError("unsupported_status") from error


def _map_diff_status(value: str) -> str:
    if not value:
        raise ValueError("empty_diff_status")
    code = value[0]
    try:
        return _STATUS_VALUES[code]
    except KeyError as error:
        raise ValueError("unsupported_diff_status") from error


def _decode_path(value: bytes) -> str:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("invalid_path_encoding") from error
    normalized = decoded.replace("\\", "/").strip("/")
    parts = PurePosixPath(normalized).parts
    if (
        not normalized
        or normalized == "."
        or "\0" in normalized
        or ".." in parts
        or (parts and parts[0].endswith(":"))
    ):
        raise ValueError("invalid_git_path")
    return normalized


def _decode_text(
    value: bytes, command: str, issues: List[GitCollectionIssue]
) -> Optional[str]:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        _add_issue(issues, "invalid_git_output", command, True)
        return None


def _decode_commit(
    value: bytes, command: str, issues: List[GitCollectionIssue]
) -> Optional[str]:
    decoded = _decode_text(value.strip(), command, issues)
    if decoded is None or not _COMMIT_HASH.fullmatch(decoded):
        if decoded is not None:
            _add_issue(issues, "invalid_commit_hash", command, False)
        return None
    return decoded.lower()


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve(strict=False))) == os.path.normcase(
        str(second.resolve(strict=False))
    )


def _path_key(value: str) -> str:
    return value.casefold()


def _change_id(workspace_id: str, path: str) -> str:
    source = "\0".join((workspace_id, path.casefold()))
    return "change_" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _merge_scopes(
    first: Sequence[str], second: Sequence[str]
) -> Tuple[str, ...]:
    order = ("committed_in_session", "staged", "worktree")
    values = set((*first, *second))
    return tuple(scope for scope in order if scope in values)


def _not_applicable() -> GitNumstat:
    return GitNumstat("not_applicable", None, None)


def _failed_numstat() -> GitNumstat:
    return GitNumstat("failed", None, None)


def _add_issue(
    issues: List[GitCollectionIssue], kind: str, command: str, retryable: bool
) -> None:
    issue = GitCollectionIssue(kind, command, retryable)
    if issue in issues:
        return
    issues.append(issue)
    LOGGER.warning(
        "Git変更収集警告: command=%s kind=%s retryable=%s",
        command,
        kind,
        retryable,
    )


def _failed_result(
    root_name: Optional[str],
    issues: Sequence[GitCollectionIssue],
    *,
    head: Optional[str] = None,
    branch: Optional[str] = None,
) -> GitCollectionResult:
    repository = GitRepositoryState(
        collection_status="failed",
        root_name=root_name,
        branch=branch,
        head=head,
        session_start_commit=None,
        session_start_source=None,
        clean=False,
    )
    return GitCollectionResult(
        repository,
        tuple(),
        tuple(issues),
        any(issue.retryable for issue in issues),
    )
