"""Docker-host control only; all Store/lease code runs inside the existing API.

Keep an exclusive lease through `docker stop`. A live stdin pipe is the lease
holder's lifetime, avoiding detached processes, stale ready files, and timeouts
that silently reopen admission. It also bootstraps releases before the durable
deployment fence was introduced.
"""
from __future__ import annotations

import argparse
import json
import selectors
import subprocess  # nosec B404
from pathlib import Path


def docker(*args: str, **kwargs):
    # Fixed Docker executable and argument arrays, with no shell expansion.
    return subprocess.run(  # nosec B603, B607
        ["docker", *args], check=True, text=True, capture_output=True, **kwargs,
    )


def inspect_api() -> dict | None:
    try:
        return json.loads(docker("inspect", "revguard-api").stdout)[0]
    except subprocess.CalledProcessError:
        # A live daemon is not proof that the API is absent. An inspect error
        # on an existing container must never bypass its runtime lease.
        listed = docker("container", "ls", "--all", "--filter", "name=^/revguard-api$",
                        "--format", "{{.ID}}", timeout=15)
        if listed.stdout.strip():
            raise
        return None


def check_topology(profile: str, project: str) -> dict | None:
    current = inspect_api()
    if not current:
        return None
    config = current["Config"]
    if config.get("Labels", {}).get("com.docker.compose.project") != project:
        raise RuntimeError("现有 API 属于其他 Compose 项目；停止部署，避免切换到空数据卷")
    env = dict(item.split("=", 1) for item in config["Env"] if "=" in item)
    if bool(env.get("REVGUARD_DATABASE_URL")) != (profile == "full"):
        raise RuntimeError("当前数据库拓扑与部署参数不同；请使用原拓扑，数据库迁移必须单独执行")
    return current


def stop_quiescent(owner: str) -> None:
    current = inspect_api()
    if not current or not current["State"]["Running"]:
        return
    # Load just this stdlib module into the old container; do not require the old
    # image to contain the new module or write to its read-only root filesystem.
    module = (Path(__file__).resolve().parents[1] / "revguard/deployment.py").read_text()
    source = (
        "import sys,os\nfrom pathlib import Path\n"
        "Path('/tmp/revguard-deploy-'+sys.argv[1]+'.pid').write_text(str(os.getpid()))\n"
        "from revguard.api import store,gateway\n"
        "from revguard.runtime_barrier import acquire_runtime_lease,assert_recording_quiescent\n"
        "namespace={'__file__':'/app/revguard/deployment.py'}\n"
        f"exec({module!r},namespace)\n"
        "with acquire_runtime_lease(store,exclusive=True):\n"
        "    assert_recording_quiescent(store,gateway.journal)\n"
        "    namespace['prepare_fence'](sys.argv[1])\n"
        "    print('QUIESCENT',flush=True)\n"
        "    sys.stdin.buffer.read()\n"
    )
    # The inline source contains no credentials and is passed without a shell.
    process = subprocess.Popen(  # nosec B603, B607
        ["docker", "exec", "-i", "revguard-api", "python", "-u", "-c", source, owner],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20) or process.stdout.readline() != b"QUIESCENT\n":
                raise RuntimeError("无法取得静止运行租约；当前请求、后台任务或待对账资金仍需处理")
        if process.poll() is not None:
            raise RuntimeError("运行租约已退出；停止部署")
        docker("stop", "--time", "30", "revguard-api", timeout=45)
        if inspect_api()["State"]["Running"]:
            raise RuntimeError("旧 API 未停止；停止部署")
        print("旧 API 已在独占运行租约内停止；维护标记保留")
    finally:
        if process.stdin:
            process.stdin.close()
        # Closing docker exec stdin does not reliably end a remote read().
        # Address only this invocation's PID, checking its unguessable owner in
        # cmdline before signalling; never kill other API/Worker processes.
        cleanup = (
            "import os,signal,sys\nfrom pathlib import Path\n"
            "p=Path('/tmp/revguard-deploy-'+sys.argv[1]+'.pid')\n"
            "if p.exists():\n"
            "    pid=int(p.read_text())\n"
            "    command=Path('/proc/'+str(pid)+'/cmdline')\n"
            "    if command.exists() and command.read_bytes().rstrip(b'\\0').split(b'\\0')[-1]==sys.argv[1].encode():\n"
            "        os.kill(pid,signal.SIGTERM)\n"
            "    p.unlink(missing_ok=True)\n"
        )
        try:
            docker("exec", "revguard-api", "python", "-c", cleanup, owner, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass  # The normal successful path has already stopped the container.
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preflight", "stop"])
    parser.add_argument("--profile", choices=["local", "full"], required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--owner")
    args = parser.parse_args()
    try:
        check_topology(args.profile, args.project)
        if args.action == "stop":
            if not args.owner:
                raise ValueError("Missing deployment owner")
            stop_quiescent(args.owner)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    except (OSError, subprocess.SubprocessError, KeyError, TypeError):
        # Never print docker inspect output (it contains deployment secrets).
        raise SystemExit("Docker 部署检查失败；未开放业务入口，请核对容器状态") from None


if __name__ == "__main__":
    main()
