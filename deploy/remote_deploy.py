#!/usr/bin/env python3
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import paramiko

DEFAULT_HOST = "172.20.120.11"
DEFAULT_USER = "fzkj"
DEFAULT_REMOTE_DIR = "/home/fzkj/scraper"
SKIP_DIRS = {".git", ".venv", "__pycache__", ".vscode", ".cursor"}
SKIP_FILES = {".env"}
SYNC_ROOTS = ("src", "configs", "deploy", "sql", "tests", "docs")
SYNC_FILES = ("requirements.txt", "pyproject.toml", "README.md", ".env.example")


def _collect_files(project_root: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for rel in SYNC_FILES:
        path = project_root / rel
        if path.is_file():
            files.append((path, rel.replace("\\", "/")))
    for root_name in SYNC_ROOTS:
        root = project_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(project_root).as_posix()
            parts = set(path.parts)
            if parts & SKIP_DIRS:
                continue
            if path.name in SKIP_FILES:
                continue
            files.append((path, rel))
    return files


def _run(client: paramiko.SSHClient, command: str, *, timeout: int = 120) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if err.strip():
        out = f"{out.rstrip()}\n{err}".strip()
    return out


def _run_sudo(client: paramiko.SSHClient, command: str, *, password: str, timeout: int = 120) -> str:
    escaped = command.replace("'", "'\\''")
    return _run(client, f"echo '{password}' | sudo -S bash -lc '{escaped}'", timeout=timeout)


def _ensure_remote_env(client: paramiko.SSHClient, remote_dir: str) -> None:
    env_path = f"{remote_dir}/.env"
    _, stdout, _ = client.exec_command(f"test -f {env_path} && cat {env_path} || true", timeout=30)
    current = stdout.read().decode("utf-8", errors="replace")
    additions = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "Qwer@20250124",
        "APP_SECRET_KEY": secrets.token_hex(24),
        "APP_LOG_DIR": f"{remote_dir}/logs",
        "BOCHA_REQUEST_DELAY_SECONDS": "2",
        "BOCHA_MAX_RETRIES": "4",
        "BOCHA_RETRY_BACKOFF_SECONDS": "2",
        "NOTIFY_RESEND": "true",
    }
    missing_lines: list[str] = []
    for key, value in additions.items():
        if f"{key}=" not in current:
            missing_lines.append(f"{key}={value}")
    if not missing_lines:
        return
    payload = "\n".join(missing_lines) + "\n"
    sftp = client.open_sftp()
    with sftp.file(f"{remote_dir}/.env.append", "w") as handle:
        handle.write(payload)
    sftp.close()
    _run(client, f"cat {remote_dir}/.env.append >> {remote_dir}/.env && rm {remote_dir}/.env.append")


def _ensure_notify_resend(client: paramiko.SSHClient, remote_dir: str) -> None:
    env_path = f"{remote_dir}/.env"
    cmd = (
        f"if grep -q '^NOTIFY_RESEND=' {env_path}; then "
        f"sed -i 's/^NOTIFY_RESEND=.*/NOTIFY_RESEND=true/' {env_path}; "
        f"else echo 'NOTIFY_RESEND=true' >> {env_path}; fi"
    )
    _run(client, cmd)


def deploy(
    *,
    host: str,
    user: str,
    password: str,
    remote_dir: str,
    install_web: bool,
    run_smoke: bool,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=20, allow_agent=False, look_for_keys=False)

    print(f"[1/6] Sync files to {host}:{remote_dir}")
    sftp = client.open_sftp()
    _run(client, f"mkdir -p {remote_dir}/logs")
    for local_path, rel_path in _collect_files(project_root):
        remote_path = f"{remote_dir}/{rel_path}"
        remote_parent = str(Path(remote_path).parent).replace("\\", "/")
        _run(client, f"mkdir -p {remote_parent}")
        sftp.put(str(local_path), remote_path)
        print(f"  uploaded {rel_path}")
    sftp.close()

    print("[2/6] Install Python dependencies")
    install_out = _run(
        client,
        f"cd {remote_dir} && test -x .venv/bin/python || python3 -m venv .venv; "
        f".venv/bin/pip install -r requirements.txt -q",
        timeout=300,
    )
    if install_out.strip():
        print(install_out)

    print("[3/6] Ensure server .env has admin and Bocha settings")
    _ensure_remote_env(client, remote_dir)
    _ensure_notify_resend(client, remote_dir)

    print("[4/6] Update systemd units")
    unit_cmds = [
        f"cp {remote_dir}/deploy/systemd/zj-agent-daily.service /etc/systemd/system/zj-agent-daily.service",
        f"cp {remote_dir}/deploy/systemd/zj-agent-daily.timer /etc/systemd/system/zj-agent-daily.timer",
        f"cp {remote_dir}/deploy/systemd/zj-agent-weekly.service /etc/systemd/system/zj-agent-weekly.service",
        f"cp {remote_dir}/deploy/systemd/zj-agent-weekly.timer /etc/systemd/system/zj-agent-weekly.timer",
        "systemctl daemon-reload",
        "systemctl disable --now zj-agent-weekly.timer || true",
        "systemctl enable zj-agent-daily.timer",
        "systemctl restart zj-agent-daily.timer",
        "systemctl reset-failed zj-agent-daily.service || true",
        "systemctl reset-failed zj-agent-weekly.service || true",
    ]
    if install_web:
        unit_cmds.insert(
            2,
            f"cp {remote_dir}/deploy/systemd/zj-agent-web.service /etc/systemd/system/zj-agent-web.service",
        )
        unit_cmds.extend(
            [
                "systemctl enable zj-agent-web.service",
                "systemctl restart zj-agent-web.service",
            ]
        )
    for cmd in unit_cmds:
        out = _run_sudo(client, cmd, password=password, timeout=120)
        if out.strip():
            print(out)

    print("[5/6] Service status")
    status_cmds = [
        "systemctl status zj-agent-daily.timer --no-pager",
        "systemctl status zj-agent-daily.service --no-pager || true",
    ]
    if install_web:
        status_cmds.append("systemctl status zj-agent-web.service --no-pager")
    for cmd in status_cmds:
        print(_run_sudo(client, cmd, password=password, timeout=60))

    if run_smoke:
        print("[6/6] Smoke tests")
        smoke_cmds = [
            f"cd {remote_dir} && PYTHONPATH=src .venv/bin/python -m zj_agent.cli notify feishu-test --text 'zj-agent deploy smoke test'",
            f"cd {remote_dir} && PYTHONPATH=src .venv/bin/python -m unittest tests.test_search tests.test_filters tests.test_fetcher -v",
        ]
        for cmd in smoke_cmds:
            print(_run(client, cmd, timeout=600))

    client.close()
    print("Deploy complete.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy scraper project to remote Linux server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", required=True)
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    parser.add_argument("--no-web", action="store_true", help="Skip zj-agent-web.service install")
    parser.add_argument("--no-smoke", action="store_true", help="Skip post-deploy smoke tests")
    args = parser.parse_args()
    deploy(
        host=args.host,
        user=args.user,
        password=args.password,
        remote_dir=args.remote_dir,
        install_web=not args.no_web,
        run_smoke=not args.no_smoke,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
