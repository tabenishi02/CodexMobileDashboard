import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

from tools.git_change_collector import (
    GitCommandResult,
    GitCommandRunner,
    collect_git_changes,
    parse_name_status,
    parse_numstat,
    parse_porcelain_status,
)


class FailingNumstatRunner(GitCommandRunner):
    def run(
        self,
        workspace: Path,
        arguments: Sequence[str],
        timeout_seconds: int,
    ) -> GitCommandResult:
        if tuple(arguments) == (
            "diff",
            "--numstat",
            "-z",
            "--find-renames",
            "--find-copies",
        ):
            raise subprocess.TimeoutExpired("git", timeout_seconds)
        return super().run(workspace, arguments, timeout_seconds)


class GitOutputParserTests(unittest.TestCase):
    def test_porcelain_status_includes_rare_states_and_rename(self) -> None:
        data = (
            b"T  link.txt\0"
            b" C copied.txt\0source.txt\0"
            b"UU conflict.txt\0"
            b"?? new.bin\0"
            b"R  new.txt\0old.txt\0"
        )

        entries = parse_porcelain_status(data)

        self.assertEqual("type_changed", entries[0].index)
        self.assertEqual("copied", entries[1].worktree)
        self.assertEqual("source.txt", entries[1].old_path)
        self.assertEqual(("conflicted", "conflicted"), (entries[2].index, entries[2].worktree))
        self.assertEqual(("none", "untracked"), (entries[3].index, entries[3].worktree))
        self.assertEqual(("new.txt", "old.txt"), (entries[4].path, entries[4].old_path))

    def test_name_status_accepts_nul_and_tab_separators(self) -> None:
        entries = parse_name_status(
            b"M\0one.txt\0R100\0old.txt\0new.txt\0A\tthree.txt\0"
        )

        self.assertEqual("modified", entries[0].status)
        self.assertEqual(("new.txt", "old.txt", "renamed"), (entries[1].path, entries[1].old_path, entries[1].status))
        self.assertEqual(("three.txt", "added"), (entries[2].path, entries[2].status))

    def test_numstat_distinguishes_empty_binary_and_rename(self) -> None:
        entries = parse_numstat(
            b"0\t0\tempty.txt\0-\t-\timage.png\0"
            b"1\t2\t\0old.txt\0new.txt\0"
        )

        self.assertEqual(("measured", 0, 0), (entries[0].value.state, entries[0].value.added, entries[0].value.deleted))
        self.assertEqual(("binary", None, None), (entries[1].value.state, entries[1].value.added, entries[1].value.deleted))
        self.assertEqual(("new.txt", "old.txt"), (entries[2].path, entries[2].old_path))


@unittest.skipUnless(shutil.which("git"), "Git is required for integration tests")
class GitChangeCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name) / "Project"
        self.workspace.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.invalid")
        (self.workspace / "README.md").write_text("initial\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "initial")
        self.start_commit = self.git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def git(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.workspace), *arguments],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )

    def test_collects_committed_staged_unstaged_and_untracked(self) -> None:
        (self.workspace / "committed.txt").write_text("committed\n", encoding="utf-8")
        self.git("add", "committed.txt")
        self.git("commit", "-m", "committed in session")
        (self.workspace / "README.md").write_text("initial\nchanged\n", encoding="utf-8")
        (self.workspace / "staged.txt").write_text("staged\n", encoding="utf-8")
        self.git("add", "staged.txt")
        (self.workspace / "untracked.png").write_bytes(b"not inspected")

        result = collect_git_changes(
            self.workspace,
            "workspace-1",
            jsonl_session_start_commit=self.start_commit,
        )

        self.assertEqual("ok", result.repository.collection_status)
        self.assertEqual("jsonl", result.repository.session_start_source)
        self.assertFalse(result.repository.clean)
        by_path = {change.path: change for change in result.files}
        self.assertEqual("added", by_path["committed.txt"].status.committed_in_session)
        self.assertEqual("measured", by_path["committed.txt"].committed_in_session.state)
        self.assertEqual("modified", by_path["README.md"].status.worktree)
        self.assertEqual((1, 0), (by_path["README.md"].unstaged.added, by_path["README.md"].unstaged.deleted))
        self.assertEqual("added", by_path["staged.txt"].status.index)
        self.assertEqual("not_inspected", by_path["untracked.png"].unstaged.state)
        self.assertIsNone(by_path["untracked.png"].binary)

    def test_empty_staged_file_is_measured_as_zero_lines(self) -> None:
        (self.workspace / "empty.txt").write_bytes(b"")
        self.git("add", "empty.txt")

        result = collect_git_changes(self.workspace, "workspace-1")

        change = result.files[0]
        self.assertEqual("measured", change.staged.state)
        self.assertEqual((0, 0), (change.staged.added, change.staged.deleted))
        self.assertFalse(change.binary)

    def test_staged_binary_is_identified_by_git(self) -> None:
        (self.workspace / "image.bin").write_bytes(b"before\x00after")
        self.git("add", "image.bin")

        result = collect_git_changes(self.workspace, "workspace-1")

        change = result.files[0]
        self.assertEqual("binary", change.staged.state)
        self.assertTrue(change.binary)

    def test_committed_rename_uses_git_old_and_new_paths(self) -> None:
        self.git("mv", "README.md", "GUIDE.md")
        self.git("commit", "-m", "rename")

        result = collect_git_changes(
            self.workspace,
            "workspace-1",
            jsonl_session_start_commit=self.start_commit,
        )

        change = result.files[0]
        self.assertEqual("GUIDE.md", change.path)
        self.assertEqual("README.md", change.old_path)
        self.assertEqual("renamed", change.status.committed_in_session)
        self.assertEqual("measured", change.committed_in_session.state)

    def test_staged_rename_uses_git_old_and_new_paths(self) -> None:
        self.git("mv", "README.md", "GUIDE.md")

        result = collect_git_changes(self.workspace, "workspace-1")

        change = result.files[0]
        self.assertEqual("GUIDE.md", change.path)
        self.assertEqual("README.md", change.old_path)
        self.assertEqual("renamed", change.status.index)
        self.assertEqual("measured", change.staged.state)

    def test_change_id_is_stable_and_has_no_timestamp(self) -> None:
        (self.workspace / "README.md").write_text("changed\n", encoding="utf-8")

        first = collect_git_changes(self.workspace, "workspace-1")
        second = collect_git_changes(self.workspace, "workspace-1")

        self.assertEqual(first.files[0].change_id, second.files[0].change_id)
        self.assertRegex(first.files[0].change_id, r"^change_[0-9a-f]{64}$")

    def test_numstat_timeout_keeps_status_and_requests_retry(self) -> None:
        (self.workspace / "README.md").write_text("changed\n", encoding="utf-8")

        result = collect_git_changes(
            self.workspace,
            "workspace-1",
            runner=FailingNumstatRunner(),
        )

        self.assertEqual("warning", result.repository.collection_status)
        self.assertTrue(result.retry_required)
        self.assertEqual("modified", result.files[0].status.worktree)
        self.assertEqual("failed", result.files[0].unstaged.state)

    def test_missing_session_start_does_not_invent_committed_changes(self) -> None:
        missing = "0" * 40

        result = collect_git_changes(
            self.workspace,
            "workspace-1",
            jsonl_session_start_commit=missing,
        )

        self.assertEqual("warning", result.repository.collection_status)
        self.assertEqual(tuple(), result.files)
        self.assertTrue(any(issue.command == "validate_session_start" for issue in result.issues))

    def test_non_repository_is_failed(self) -> None:
        outside = Path(self.directory.name) / "NotGit"
        outside.mkdir()

        result = collect_git_changes(outside, "workspace-1")

        self.assertEqual("failed", result.repository.collection_status)
        self.assertEqual(tuple(), result.files)


if __name__ == "__main__":
    unittest.main()
