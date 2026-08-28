from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchTarget:
    stage: str
    zone: str
    branch: str
    zone_directory: str
    worktree_path: Path
    commit_sha: str

    @property
    def scope(self) -> str:
        return f"{self.stage}_{self.zone}"

    def patterns(self, patterns: Iterable[str]) -> list[str]:
        """Prefix zone-relative glob patterns with INT/EXT directory."""
        prefix = self.zone_directory.strip("/\\")
        result: list[str] = []
        for pattern in patterns:
            normalized = pattern.lstrip("/\\")
            result.append(f"{prefix}/{normalized}")
        return result


class EnvironmentManager:
    """Maintains read-only Git worktrees for logical deployment stages.

    The source repository is never switched by this class. Each configured
    stage gets a detached worktree that is automatically created and refreshed
    from the configured Git branch.

    Relative worktree_root values are resolved against the target Gravitee
    repository, not against the MCP server directory.
    """

    def __init__(
        self,
        repository: str | Path,
        config: dict[str, Any],
        *,
        base_dir: str | Path,
    ) -> None:
        self.repository = Path(repository).expanduser().resolve()
        self.base_dir = Path(base_dir).expanduser().resolve()

        if not isinstance(config, dict):
            raise ValueError("environments config must be an object")

        self.default_scope = config.get("default_scope", "test_int")
        self.remote = config.get("remote", "origin")
        self.refresh_on_startup = config.get("refresh_on_startup", True)
        raw_worktree_root = config.get("worktree_root", "./.worktrees")
        self.stages = config.get("stages", {})
        self.zones = config.get("zones", {})

        if not isinstance(self.default_scope, str) or not self.default_scope:
            raise ValueError("environments.default_scope must be a non-empty string")
        if not isinstance(self.remote, str) or not self.remote:
            raise ValueError("environments.remote must be a non-empty string")
        if not isinstance(self.refresh_on_startup, bool):
            raise ValueError("environments.refresh_on_startup must be boolean")
        if not isinstance(raw_worktree_root, str) or not raw_worktree_root:
            raise ValueError("environments.worktree_root must be a non-empty string")
        if not isinstance(self.stages, dict) or not self.stages:
            raise ValueError("environments.stages must be a non-empty object")
        if not isinstance(self.zones, dict) or not self.zones:
            raise ValueError("environments.zones must be a non-empty object")

        for stage, stage_config in self.stages.items():
            if not isinstance(stage, str) or not stage:
                raise ValueError("environment stage names must be non-empty strings")
            if not isinstance(stage_config, dict):
                raise ValueError(f"environments.stages.{stage} must be an object")
            branch = stage_config.get("branch")
            if not isinstance(branch, str) or not branch:
                raise ValueError(
                    f"environments.stages.{stage}.branch must be a non-empty string"
                )

        for zone, directory in self.zones.items():
            if not isinstance(zone, str) or not zone:
                raise ValueError("environment zone names must be non-empty strings")
            if not isinstance(directory, str) or not directory:
                raise ValueError(
                    f"environments.zones.{zone} must be a non-empty directory string"
                )

        root = Path(raw_worktree_root).expanduser()
        repository_id = sha256(str(self.repository).encode("utf-8")).hexdigest()[:12]

        if root.is_absolute():
            # Keep absolute roots safe for possible use by several repositories.
            self.worktree_root = (root / repository_id).resolve()
            self._legacy_worktree_root = self.worktree_root
        else:
            # Relative roots belong to the repository being inspected.
            self.worktree_root = (self.repository / root).resolve()
            # Previous versions resolved the same relative root against the MCP
            # server directory and added a repository hash. Keep that location
            # so we can migrate our already-created worktrees automatically.
            self._legacy_worktree_root = (
                self.base_dir / root / repository_id
            ).resolve()

        self._worktrees: dict[str, Path] = {}
        self._commit_shas: dict[str, str] = {}

        self._validate_repository()
        self._ensure_worktree_root_excluded()
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_worktrees()
        self.initialize()
        self.resolve_scope(self.default_scope)

        logger.info("Managed worktree root: %s", self.worktree_root)

    def _run_git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(cwd or self.repository), *args]
        logger.debug("Running git command: %s", command)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Git command failed ({result.returncode}): {' '.join(command)}\n"
                f"stdout: {result.stdout.strip()}\n"
                f"stderr: {result.stderr.strip()}"
            )
        return result

    def _validate_repository(self) -> None:
        if not self.repository.exists() or not self.repository.is_dir():
            raise FileNotFoundError(f"Repository does not exist: {self.repository}")
        self._run_git("rev-parse", "--git-dir")
        logger.info("Git repository validated: %s", self.repository)

    def _ensure_worktree_root_excluded(self) -> None:
        """Hide managed nested worktrees from the source repo's git status.

        Uses .git/info/exclude, so the target repository's tracked .gitignore is
        not modified.
        """
        try:
            relative = self.worktree_root.relative_to(self.repository)
        except ValueError:
            return

        git_path = self._run_git("rev-parse", "--git-path", "info/exclude").stdout.strip()
        exclude_file = Path(git_path)
        if not exclude_file.is_absolute():
            exclude_file = (self.repository / exclude_file).resolve()

        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        pattern = f"/{relative.as_posix().strip('/')}/"

        existing = ""
        if exclude_file.exists():
            existing = exclude_file.read_text(encoding="utf-8", errors="replace")

        lines = {line.strip() for line in existing.splitlines()}
        if pattern in lines:
            return

        with exclude_file.open("a", encoding="utf-8") as file:
            if existing and not existing.endswith("\n"):
                file.write("\n")
            file.write(pattern + "\n")

        logger.info("Added managed worktree root to Git exclude: %s", pattern)

    def _migrate_legacy_worktrees(self) -> None:
        """Move worktrees created by the previous MCP-relative layout."""
        if self._legacy_worktree_root == self.worktree_root:
            return
        if not self._legacy_worktree_root.exists():
            return

        logger.info(
            "Legacy worktree root detected; migrating %s -> %s",
            self._legacy_worktree_root,
            self.worktree_root,
        )

        for stage in self.stages:
            old_path = (self._legacy_worktree_root / stage).resolve()
            new_path = (self.worktree_root / stage).resolve()

            if not old_path.exists() or new_path.exists():
                continue

            new_path.parent.mkdir(parents=True, exist_ok=True)
            result = self._run_git(
                "worktree",
                "move",
                str(old_path),
                str(new_path),
                check=False,
            )
            if result.returncode == 0:
                logger.info(
                    "Migrated managed worktree stage=%s path=%s",
                    stage,
                    new_path,
                )
            else:
                logger.warning(
                    "Could not migrate legacy worktree stage=%s; it will be left "
                    "untouched. stderr=%s",
                    stage,
                    result.stderr.strip(),
                )

        # Best-effort cleanup of empty legacy directories only.
        current = self._legacy_worktree_root
        for _ in range(2):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _ref_exists(self, ref: str) -> bool:
        return self._run_git(
            "rev-parse",
            "--verify",
            "--quiet",
            ref,
            check=False,
        ).returncode == 0

    def _stage_ref(self, branch: str) -> str:
        remote_ref = f"{self.remote}/{branch}"
        if self._ref_exists(remote_ref):
            return remote_ref
        if self._ref_exists(branch):
            logger.warning(
                "Remote ref %s not found; using local branch/ref %s",
                remote_ref,
                branch,
            )
            return branch
        raise RuntimeError(
            f"Neither '{remote_ref}' nor local ref '{branch}' exists in {self.repository}"
        )

    def initialize(self) -> None:
        """Create managed worktrees and optionally refresh remote refs first."""
        self._run_git("worktree", "prune", check=False)

        if self.refresh_on_startup:
            fetch = self._run_git("fetch", self.remote, "--prune", check=False)
            if fetch.returncode != 0:
                logger.warning(
                    "git fetch failed; using existing refs. stderr=%s",
                    fetch.stderr.strip(),
                )
            else:
                logger.info("Git fetch completed for remote=%s", self.remote)

        for stage, stage_config in self.stages.items():
            self._prepare_stage(stage, stage_config["branch"])

    def _prepare_stage(self, stage: str, branch: str) -> None:
        ref = self._stage_ref(branch)
        worktree = (self.worktree_root / stage).resolve()

        if worktree.exists():
            git_marker = worktree / ".git"
            if not git_marker.exists():
                if any(worktree.iterdir()):
                    raise RuntimeError(
                        f"Managed worktree path exists but is not a Git worktree: {worktree}"
                    )
                worktree.rmdir()

        if not worktree.exists():
            worktree.parent.mkdir(parents=True, exist_ok=True)
            self._run_git("worktree", "add", "--detach", str(worktree), ref)
            logger.info("Created worktree stage=%s ref=%s path=%s", stage, ref, worktree)
        else:
            self._run_git("reset", "--hard", ref, cwd=worktree)
            logger.info("Updated worktree stage=%s ref=%s path=%s", stage, ref, worktree)

        commit_sha = self._run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self._worktrees[stage] = worktree
        self._commit_shas[stage] = commit_sha
        logger.info("Stage ready stage=%s branch=%s commit=%s", stage, branch, commit_sha)

    def refresh(self) -> None:
        """Fetch remote changes and reset all managed worktrees to stage refs."""
        fetch = self._run_git("fetch", self.remote, "--prune", check=False)
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch.stderr.strip()}")
        for stage, stage_config in self.stages.items():
            self._prepare_stage(stage, stage_config["branch"])

    def resolve_scope(self, scope: str | None = None) -> list[SearchTarget]:
        """Resolve stage_zone, stage, or all into concrete targets."""
        value = (scope or self.default_scope).strip().lower()

        pairs: list[tuple[str, str]] = []
        if value == "all":
            pairs = [
                (stage, zone)
                for stage in self.stages
                for zone in self.zones
            ]
        elif value in self.stages:
            pairs = [(value, zone) for zone in self.zones]
        else:
            if "_" not in value:
                raise ValueError(
                    f"Unknown scope '{value}'. Expected stage_zone, stage, or all"
                )
            stage, zone = value.rsplit("_", 1)
            if stage not in self.stages or zone not in self.zones:
                raise ValueError(
                    f"Unknown scope '{value}'. Stages={list(self.stages)}, "
                    f"zones={list(self.zones)}"
                )
            pairs = [(stage, zone)]

        targets: list[SearchTarget] = []
        for stage, zone in pairs:
            stage_config = self.stages[stage]
            targets.append(
                SearchTarget(
                    stage=stage,
                    zone=zone,
                    branch=stage_config["branch"],
                    zone_directory=self.zones[zone],
                    worktree_path=self._worktrees[stage],
                    commit_sha=self._commit_shas[stage],
                )
            )
        return targets

    def cache_context(self, scope: str | None = None) -> dict[str, Any]:
        """Stable cache context; commit SHA invalidates stale branch results."""
        return {
            "scope": (scope or self.default_scope).strip().lower(),
            "targets": [
                {
                    "stage": target.stage,
                    "zone": target.zone,
                    "commit": target.commit_sha,
                }
                for target in self.resolve_scope(scope)
            ],
        }

    def targets_for_file(
        self,
        file_path: str,
        scope: str | None = None,
    ) -> list[SearchTarget]:
        """Resolve targets compatible with zone encoded in repo-relative filepath."""
        normalized = file_path.replace("\\", "/").lstrip("/")
        resolved = self.resolve_scope(scope)
        matching = [
            target
            for target in resolved
            if normalized.lower().startswith(
                target.zone_directory.replace("\\", "/").strip("/").lower() + "/"
            )
        ]
        if not matching:
            raise ValueError(
                f"File '{file_path}' does not belong to any zone selected by "
                f"scope '{scope or self.default_scope}'"
            )

        unique: dict[str, SearchTarget] = {}
        for target in matching:
            unique[target.stage] = target
        return list(unique.values())
