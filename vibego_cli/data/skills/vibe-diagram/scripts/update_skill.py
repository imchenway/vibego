#!/usr/bin/env python3
"""Safely update a direct-installed Vibe Diagram skill from the stable channel."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterator, NamedTuple, Optional, Tuple


MANIFEST_URL = (
    "https://raw.githubusercontent.com/imchenway/vibe-diagram/"
    "stable/skills/vibe-diagram/update.json"
)
ARCHIVE_URL = "https://github.com/imchenway/vibe-diagram/archive/refs/tags/{ref}.zip"
VERSION_RE = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
MANIFEST_KEYS = {"schema_version", "channel", "version", "ref", "tree_sha256"}
REQUIRED_FILES = {
    "SKILL.md",
    "VERSION",
    "update.json",
    "references/runtime-workflow.md",
    "scripts/update_skill.py",
    "scripts/vibe_diagram_lint.py",
}


class UpdateError(RuntimeError):
    """The update payload or local installation violated the update contract."""


class UpdateResult(NamedTuple):
    status: str
    local_version: str
    remote_version: Optional[str] = None
    backup_path: Optional[str] = None
    message: str = ""


def parse_version(value: str) -> Tuple[int, int, int]:
    if not isinstance(value, str) or VERSION_RE.fullmatch(value) is None:
        raise UpdateError(f"version must be strict major.minor.patch: {value!r}")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def read_version(skill_root: Path) -> str:
    path = skill_root / "VERSION"
    try:
        raw = path.read_bytes()
        text = raw.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise UpdateError(f"could not read VERSION: {exc}") from exc
    if not text.endswith("\n") or text.count("\n") != 1:
        raise UpdateError("VERSION must contain one newline-terminated line")
    version = text[:-1]
    parse_version(version)
    return version


def _safe_files(skill_root: Path) -> Iterator[Tuple[str, Path]]:
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise UpdateError(f"skill root must be a real directory: {skill_root}")
    for path in sorted(skill_root.rglob("*")):
        if path.is_symlink():
            raise UpdateError(f"symlink is forbidden in the skill tree: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise UpdateError(f"non-regular object is forbidden in the skill tree: {path}")
        relative = path.relative_to(skill_root).as_posix()
        if relative == "update.json":
            continue
        yield relative, path


def tree_sha256(skill_root: Path) -> str:
    digest = hashlib.sha256()
    for relative, path in _safe_files(skill_root):
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _json_object(payload: bytes) -> Dict[str, object]:
    def unique(pairs: list) -> Dict[str, object]:
        result: Dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise UpdateError(f"duplicate manifest key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"invalid update manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise UpdateError("update manifest must be a JSON object")
    return value


def validate_manifest(value: Dict[str, object]) -> Dict[str, object]:
    if set(value) != MANIFEST_KEYS:
        raise UpdateError("update manifest has an invalid key set")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise UpdateError("update manifest schema_version must be integer 1")
    if value["channel"] != "stable":
        raise UpdateError("update manifest channel must be stable")
    version = value["version"]
    if not isinstance(version, str):
        raise UpdateError("update manifest version must be a string")
    parse_version(version)
    if value["ref"] != f"v{version}":
        raise UpdateError("update manifest ref must pin the declared version tag")
    digest = value["tree_sha256"]
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise UpdateError("update manifest tree_sha256 must be lowercase SHA-256")
    return value


def fetch_stable_manifest() -> Dict[str, object]:
    request = urllib.request.Request(
        MANIFEST_URL,
        headers={"Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "vibe-diagram-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            payload = response.read(128 * 1024 + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise OSError(f"stable manifest is unavailable: {exc}") from exc
    if len(payload) > 128 * 1024:
        raise UpdateError("update manifest exceeds 128 KiB")
    return _json_object(payload)


def fetch_release_archive(ref: str, target: Path) -> None:
    if re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", ref) is None:
        raise UpdateError(f"unsafe release ref: {ref!r}")
    request = urllib.request.Request(
        ARCHIVE_URL.format(ref=ref),
        headers={"Accept": "application/zip", "User-Agent": "vibe-diagram-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        raise OSError(f"release archive is unavailable: {exc}") from exc


def _safe_zip_name(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise UpdateError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name.rstrip("/"))
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise UpdateError(f"unsafe archive path: {name!r}")
    return path


def stage_archive(archive_path: Path, staging_root: Path) -> Path:
    candidate = staging_root / "candidate"
    candidate.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            version_markers = []
            for info in members:
                path = _safe_zip_name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise UpdateError(f"archive symlink is forbidden: {info.filename}")
                if len(path.parts) == 4 and path.parts[1:] == (
                    "skills",
                    "vibe-diagram",
                    "VERSION",
                ):
                    version_markers.append(path.parent)
            if len(set(version_markers)) != 1:
                raise UpdateError("release archive must contain one skill VERSION marker")
            prefix = version_markers[0]
            seen = set()
            for info in members:
                path = _safe_zip_name(info.filename)
                try:
                    relative = path.relative_to(prefix)
                except ValueError:
                    continue
                if not relative.parts:
                    continue
                relative_text = relative.as_posix()
                if relative_text in seen:
                    raise UpdateError(f"duplicate archive entry: {relative_text}")
                seen.add(relative_text)
                target = candidate.joinpath(*relative.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"invalid release archive: {exc}") from exc
    return candidate


def _managed_package(skill_root: Path, local_version: str) -> bool:
    if skill_root.parent.name != "skills":
        return False
    package_root = skill_root.parent.parent
    if not (package_root / "LICENSE").is_file():
        return False
    if (package_root / "VERSION").is_file():
        return True
    candidates = [path for path in package_root.glob("*.json") if path.is_file()]
    for directory in package_root.iterdir():
        if directory.is_dir() and not directory.is_symlink() and directory != skill_root.parent:
            candidates.extend(path for path in directory.glob("*.json") if path.is_file())
    for path in candidates:
        try:
            if path.stat().st_size > 128 * 1024:
                continue
            manifest = _json_object(path.read_bytes())
        except (OSError, UpdateError):
            continue
        if manifest.get("name") == "vibe-diagram" and manifest.get("version") == local_version:
            return True
    return False


def _backup_root(skill_root: Path) -> Path:
    if skill_root.parent.name == "skills":
        return skill_root.parent.parent / "backups" / "skills"
    return skill_root.parent / ".vibe-diagram-backups"


@contextlib.contextmanager
def _update_lock(skill_root: Path, timeout: float = 10.0) -> Iterator[None]:
    lock_path = skill_root.parent / ".vibe-diagram-update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0)
    if handle.read(1) != b"0":
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise UpdateError("timed out waiting for the update lock") from exc
                time.sleep(0.05)
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _verify_candidate(candidate: Path, manifest: Dict[str, object]) -> None:
    actual_files = {relative for relative, _path in _safe_files(candidate)} | {"update.json"}
    missing = sorted(REQUIRED_FILES - actual_files)
    if missing:
        raise UpdateError(f"release is missing required files: {', '.join(missing)}")
    version = read_version(candidate)
    if version != manifest["version"]:
        raise UpdateError("release VERSION does not match the stable manifest")
    local_manifest = validate_manifest(_json_object((candidate / "update.json").read_bytes()))
    if local_manifest != manifest:
        raise UpdateError("release manifest does not match the stable manifest")
    if tree_sha256(candidate) != manifest["tree_sha256"]:
        raise UpdateError("release tree integrity check failed")


def _activate_candidate(skill_root: Path, candidate: Path, local_version: str) -> Path:
    backups = _backup_root(skill_root)
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    backup = backups / f"vibe-diagram-{local_version}-{stamp}"
    suffix = 1
    while backup.exists():
        backup = backups / f"vibe-diagram-{local_version}-{stamp}-{suffix}"
        suffix += 1
    os.replace(skill_root, backup)
    try:
        os.replace(candidate, skill_root)
    except BaseException:
        os.replace(backup, skill_root)
        raise
    return backup


def check_and_update(
    skill_root: Path,
    *,
    fetch_manifest: Callable[[], Dict[str, object]] = fetch_stable_manifest,
    fetch_archive: Callable[[str, Path], None] = fetch_release_archive,
) -> UpdateResult:
    skill_root = skill_root.resolve()
    try:
        local_version = read_version(skill_root)
    except UpdateError as exc:
        return UpdateResult("failed", "unknown", message=str(exc))
    if _managed_package(skill_root, local_version):
        return UpdateResult("managed", local_version, message="package manager owns this skill tree")
    try:
        raw_manifest = fetch_manifest()
    except (OSError, urllib.error.URLError) as exc:
        return UpdateResult("offline", local_version, message=str(exc))
    try:
        manifest = validate_manifest(raw_manifest)
    except UpdateError as exc:
        return UpdateResult("failed", local_version, message=str(exc))
    remote_version = str(manifest["version"])
    if parse_version(remote_version) <= parse_version(local_version):
        return UpdateResult("current", local_version, remote_version)
    try:
        with _update_lock(skill_root):
            local_version = read_version(skill_root)
            if parse_version(remote_version) <= parse_version(local_version):
                return UpdateResult("current", local_version, remote_version)
            staging = Path(tempfile.mkdtemp(prefix=".vibe-diagram-update-", dir=skill_root.parent))
            try:
                archive_path = staging / "release.zip"
                try:
                    fetch_archive(str(manifest["ref"]), archive_path)
                except (OSError, urllib.error.URLError) as exc:
                    return UpdateResult("offline", local_version, remote_version, message=str(exc))
                candidate = stage_archive(archive_path, staging)
                _verify_candidate(candidate, manifest)
                backup = _activate_candidate(skill_root, candidate, local_version)
                return UpdateResult("updated", local_version, remote_version, str(backup))
            except (OSError, UpdateError) as exc:
                return UpdateResult("failed", local_version, remote_version, message=str(exc))
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    except (OSError, UpdateError) as exc:
        return UpdateResult("failed", local_version, message=str(exc))


def rollback(skill_root: Path) -> UpdateResult:
    skill_root = skill_root.resolve()
    local_version = read_version(skill_root)
    backups = _backup_root(skill_root)
    candidates = sorted(
        (path for path in backups.glob("vibe-diagram-*") if path.is_dir() and not path.is_symlink()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    if not candidates:
        return UpdateResult("failed", local_version, message="no recoverable backup exists")
    selected = candidates[0]
    restored_version = read_version(selected)
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    displaced = backups / f"vibe-diagram-{local_version}-rollback-{stamp}"
    with _update_lock(skill_root):
        os.replace(skill_root, displaced)
        try:
            os.replace(selected, skill_root)
        except BaseException:
            os.replace(displaced, skill_root)
            raise
    return UpdateResult("rolled_back", local_version, restored_version, str(displaced))


def _result_payload(result: UpdateResult) -> Dict[str, object]:
    return {
        "status": result.status,
        "local_version": result.local_version,
        "remote_version": result.remote_version,
        "backup_path": result.backup_path,
        "message": result.message,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check-and-update", action="store_true")
    actions.add_argument("--force-check", action="store_true")
    actions.add_argument("--rollback", action="store_true")
    actions.add_argument("--print-tree-sha256", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    if args.print_tree_sha256:
        print(tree_sha256(args.skill_root.resolve()))
        return 0
    if args.rollback:
        try:
            result = rollback(args.skill_root)
        except (OSError, UpdateError) as exc:
            result = UpdateResult("failed", "unknown", message=str(exc))
    else:
        result = check_and_update(args.skill_root)
    if args.json:
        print(json.dumps(_result_payload(result), ensure_ascii=True, sort_keys=True))
    else:
        print(result.status)
        if result.message:
            print(result.message, file=sys.stderr)
    if args.force_check or args.rollback:
        return 0 if result.status in {"current", "updated", "rolled_back"} else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
