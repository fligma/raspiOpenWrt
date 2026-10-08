#!/usr/bin/env python3
"""rasconf Manager support module (auto-generated split)."""

from __future__ import annotations

import os
import posixpath
import stat
from pathlib import Path, PurePosixPath

import paramiko
from pathspec import GitIgnoreSpec

from manager_config import _coerce_int
from manager_const import KNOWN_HOSTS_FILE

def safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {value}")
    return path.as_posix() if value else ""


def remote_join(root: str, relative: str) -> str:
    safe_relative_path(relative)
    return posixpath.join(root, relative) if relative else root


def make_remote_directories(sftp: paramiko.SFTPClient, path: str) -> None:
    if not path.startswith("/"):
        raise ValueError("The remote directory must be an absolute path")
    current = "/"
    for part in path.strip("/").split("/"):
        if not part:
            continue
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def credential_id(username: str, host: str, port: int) -> str:
    return f"{username}@{host}:{port}"


def create_ssh_client(settings: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if KNOWN_HOSTS_FILE.exists():
        client.load_host_keys(str(KNOWN_HOSTS_FILE))
    if settings["trust_unknown_host"]:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    password = settings["password"] or None
    key_file = settings["key_file"] or None
    timeout = _coerce_int(settings.get("ssh_timeout"), 12, 3, 300)
    client.connect(
        hostname=settings["host"],
        port=settings["port"],
        username=settings["username"],
        password=password,
        key_filename=key_file,
        look_for_keys=not password and not key_file,
        allow_agent=not password and not key_file,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    if settings["trust_unknown_host"]:
        KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(KNOWN_HOSTS_FILE))
    return client


def exec_remote(client: paramiko.SSHClient, command: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a single non-interactive command. Returns (exit_code, stdout, stderr)."""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    del stdin
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def local_entries(
    source: Path,
    ignore_spec: GitIgnoreSpec | None = None,
    show_hidden: bool = False,
) -> tuple[list[str], list[str]]:
    directories: list[str] = []
    files: list[str] = []
    for current, dir_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        if not show_hidden:
            dir_names[:] = [d for d in dir_names if not d.startswith(".")]
        else:
            dir_names[:] = list(dir_names)
        retained_dirs = []
        for name in dir_names:
            directory = current_path / name
            if directory.is_symlink():
                continue
            relative = (current_path / name).relative_to(source).as_posix()
            if ignore_spec and ignore_spec.match_file(relative + "/"):
                continue
            directories.append(relative)
            retained_dirs.append(name)
        dir_names[:] = retained_dirs
        names = file_names if show_hidden else [f for f in file_names if not f.startswith(".")]
        for name in names:
            file_path = current_path / name
            if file_path.is_symlink():
                continue
            relative = file_path.relative_to(source).as_posix()
            if not ignore_spec or not ignore_spec.match_file(relative):
                files.append(relative)
    return directories, files


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"
