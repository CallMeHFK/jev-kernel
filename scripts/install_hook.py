#!/usr/bin/env python3
"""Install (or remove) the jev-kernel PreToolUse hook in an agent host.

    python3 scripts/install_hook.py --host zcode
    python3 scripts/install_hook.py --host qoder --dry-run
    python3 scripts/install_hook.py --host codex --uninstall

Every host gets its own self-contained copy under its own dot-folder, the
same shape QwenPaw's plugin install already uses:

    ~/.zcode/jev-kernel/       backend/  jev.json  ledger.jsonl
    ~/.claude/jev-kernel/      backend/  jev.json  ledger.jsonl
    ~/.codex/jev-kernel/       backend/  jev.json  ledger.jsonl
    ~/.qoder-cn/jev-kernel/    backend/  jev.json  ledger.jsonl
    (~/.qwenpaw/plugins/jev-kernel/ is QwenPaw's own install path)

Nothing rebuilds those folders, so code, config and ledger co-live there;
the dev repo can move or be deleted without breaking the hook. Re-running
the installer re-deploys the code (that is the upgrade path) and rewrites
the registered command if it drifted. A jev.json already present in the
host folder is never overwritten; on first deploy the shared
~/.jev-kernel/jev.json (early installs) is copied in as the seed.

Host hook config files (all Claude-Code-style hook schemas, verified
against the installed hosts):

    zcode   ~/.zcode/hooks.json          {"version": 1, "hooks": {...}}
    qoder   ~/.qoder-cn/settings.json    (or ~/.qoder/settings.json if that
                                          is the one that exists)
    claude  ~/.claude/settings.json
    codex   ~/.codex/hooks.json

The merge is additive and idempotent: existing hooks (e.g. openviking's)
are preserved, and the file is backed up to <path>.jev-bak before the
first write. Uninstall removes only jev-kernel's hook entry; the deployed
folder (which holds the ledger — evidence) is kept.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARKER = "jev-kernel/backend/hook.py"

# Native plugin format: manifest dir name per host (the claude-derived plugin
# store all four hosts cloned). Verified wired end-to-end for zcode only.
PLUGIN_MANIFEST_DIR = {
    "zcode": ".zcode-plugin",
    "claude": ".claude-plugin",
    "codex": ".codex-plugin",
    "qoder": ".qoder-plugin",
}


def host_home(host: str) -> Path:
    home = Path.home()
    if host == "zcode":
        return home / ".zcode"
    if host == "qoder":
        intl = home / ".qoder"
        cn = home / ".qoder-cn"
        return intl if intl.exists() and not cn.exists() else cn
    if host == "claude":
        return home / ".claude"
    if host == "codex":
        return home / ".codex"
    raise ValueError(f"unknown host {host!r}")


def host_config_path(host: str) -> Path:
    if host in ("zcode", "codex"):
        return host_home(host) / "hooks.json"
    return host_home(host) / "settings.json"


def deploy_dir(host: str) -> Path:
    return host_home(host) / "jev-kernel"


def hook_command(host: str) -> str:
    python = shutil.which("python3") or sys.executable
    target = deploy_dir(host)
    return (
        f"JEV_KERNEL_DATA_DIR={target} "
        f"{python} {target / 'backend' / 'hook.py'} --host {host} # jev-kernel"
    )


def deploy_code(host: str) -> int:
    """Copy the backend package into the host's folder; returns file count."""
    src = REPO / "backend"
    dst = deploy_dir(host) / "backend"
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(src.glob("*.py")):
        shutil.copy2(path, dst / path.name)
        count += 1
    for stale in dst.glob("*.py"):
        if not (src / stale.name).exists():
            stale.unlink()
    shutil.rmtree(dst / "__pycache__", ignore_errors=True)
    return count


def seed_config(host: str) -> str | None:
    """First deploy only: carry over an existing config, never overwrite."""
    target = deploy_dir(host) / "jev.json"
    if target.exists():
        return None
    legacy = Path.home() / ".jev-kernel" / "jev.json"
    if legacy.exists():
        shutil.copy2(legacy, target)
        return f"seeded from {legacy}"
    return None


# -- native plugin format ---------------------------------------------------

def write_plugin_package(host: str, timeout: int) -> None:
    """Turn the deploy dir into a native plugin: manifest + hooks/hooks.json."""
    root = deploy_dir(host)
    version = "0.0.0"
    manifest_src = REPO / "plugin.json"
    if manifest_src.exists():
        try:
            version = json.loads(manifest_src.read_text(encoding="utf-8"))["version"]
        except Exception:  # noqa: BLE001 - version is cosmetic here
            pass
    manifest_dir = root / PLUGIN_MANIFEST_DIR[host]
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(json.dumps({
        "name": "jev-kernel",
        "version": version,
        "description": "mu-style judgment kernel on the tool seam: a small fast "
                       "judge answers one bounded question per tool call, ledgered, "
                       "optionally denying or escalating in active mode.",
        "author": {"name": "leo"},
        "license": "MIT",
        "hooks": "./hooks/hooks.json",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    hooks_dir = root / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "hooks.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "hooks": [{"type": "command", "command": hook_command(host),
                       "timeout": timeout}],
        }]},
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cli_config_path(host: str) -> Path:
    """The config carrying plugins.dirs / plugins.enabledPlugins."""
    if host == "zcode":
        return host_home(host) / "cli" / "config.json"
    raise SystemExit(f"jev-kernel: --format plugin is verified for zcode only so far "
                     f"(pilot); {host} still uses --format hook")


def _load_plain(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(
            f"jev-kernel: {path} is not valid JSON ({e}); refusing to touch it"
        ) from e
    if not isinstance(doc, dict):
        raise SystemExit(f"jev-kernel: {path} is not a JSON object; refusing to touch it")
    return doc


def register_inline_plugin(host: str) -> bool:
    """Add the deploy dir to plugins.dirs and enable jev-kernel@inline."""
    path = cli_config_path(host)
    doc = _load_plain(path) if path.exists() else {}
    plugins = doc.setdefault("plugins", {})
    dirs = plugins.setdefault("dirs", [])
    changed = False
    root = str(deploy_dir(host))
    if root not in dirs:
        dirs.append(root)
        changed = True
    enabled = plugins.setdefault("enabledPlugins", {})
    if enabled.get("jev-kernel@inline") is not True:
        enabled["jev-kernel@inline"] = True
        changed = True
    if changed:
        _write(path, doc)
    return changed


def unregister_inline_plugin(host: str) -> bool:
    path = cli_config_path(host)
    if not path.exists():
        return False
    doc = _load_plain(path)
    plugins = doc.get("plugins") or {}
    changed = False
    root = str(deploy_dir(host))
    if root in (plugins.get("dirs") or []):
        plugins["dirs"] = [d for d in plugins["dirs"] if d != root]
        changed = True
    if "jev-kernel@inline" in (plugins.get("enabledPlugins") or {}):
        del plugins["enabledPlugins"]["jev-kernel@inline"]
        changed = True
    if changed:
        _write(path, doc)
    return changed


def load_doc(path: Path, host: str) -> dict:
    if not path.exists():
        return {"version": 1, "hooks": {}} if host in ("zcode", "codex") else {"hooks": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(
            f"jev-kernel: {path} is not valid JSON ({e}); refusing to touch it"
        ) from e
    if not isinstance(doc, dict):
        raise SystemExit(f"jev-kernel: {path} is not a JSON object; refusing to touch it")
    doc.setdefault("hooks", {})
    if host in ("zcode", "codex"):
        doc.setdefault("version", 1)
    return doc


def _our_hooks(doc: dict) -> list[dict]:
    found = []
    for blocks in (doc.get("hooks") or {}).values():
        for block in blocks if isinstance(blocks, list) else []:
            for hook in (block or {}).get("hooks") or []:
                if MARKER in str((hook or {}).get("command", "")):
                    found.append(hook)
    return found


def install(doc: dict, host: str, timeout: int) -> str:
    """Returns 'added' | 'updated' | 'unchanged'."""
    existing = _our_hooks(doc)
    want = hook_command(host)
    if existing:
        if all(h.get("command") == want for h in existing):
            return "unchanged"
        for hook in existing:  # command drifted (e.g. repo-path install) — rewrite
            hook["command"] = want
            hook.setdefault("type", "command")
            hook.setdefault("timeout", timeout)
        return "updated"
    blocks = doc["hooks"].setdefault("PreToolUse", [])
    blocks.append({
        "hooks": [{"type": "command", "command": want, "timeout": timeout}],
    })
    return "added"


def uninstall(doc: dict) -> bool:
    changed = False
    for event, blocks in list((doc.get("hooks") or {}).items()):
        if not isinstance(blocks, list):
            continue
        kept = []
        for block in blocks:
            hooks = [h for h in (block or {}).get("hooks") or []
                     if MARKER not in str((h or {}).get("command", ""))]
            if len(hooks) != len((block or {}).get("hooks") or []):
                changed = True
            if hooks:
                block["hooks"] = hooks
                kept.append(block)
        if kept:
            doc["hooks"][event] = kept
        else:
            doc["hooks"].pop(event, None)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True, choices=("zcode", "qoder", "claude", "codex"))
    parser.add_argument("--format", choices=("hook", "plugin"), default="hook",
                        help="hook: merge an entry into the host's hook config; "
                             "plugin: native plugin package (manifest + hooks/hooks.json "
                             "inside the deploy dir, registered in the plugin store). "
                             "Verified end-to-end for zcode only.")
    parser.add_argument("--timeout", type=int, default=30,
                        help="hook timeout in seconds (default 30; judges have their own timeoutMs)")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print the result, write nothing")
    args = parser.parse_args(argv)

    if args.format == "plugin":
        return _main_plugin(args)

    path = host_config_path(args.host)
    doc = load_doc(path, args.host)

    if args.uninstall:
        changed = uninstall(doc)
        if not changed:
            print(f"jev-kernel: {args.host} uninstall: nothing to do ({path})")
            return 0
        if args.dry_run:
            print(json.dumps(doc, indent=2, ensure_ascii=False))
            print(f"jev-kernel: dry run; would write {path}")
            return 0
        _write(path, doc)
        print(f"jev-kernel: {args.host} hook entry removed from {path}")
        print(f"note: {deploy_dir(args.host)} kept (it holds the ledger); delete it by hand if unwanted")
        return 0

    change = install(doc, args.host, args.timeout)
    if args.dry_run:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        print(f"jev-kernel: dry run; would write {path} and deploy to {deploy_dir(args.host)}")
        return 0

    count = deploy_code(args.host)
    seeded = seed_config(args.host)
    if change != "unchanged":
        _write(path, doc)
    print(f"jev-kernel: {args.host}: deployed {count} files to {deploy_dir(args.host)}, "
          f"hook entry {change} in {path}")
    if seeded:
        print(f"jev-kernel: config {seeded}")
    if args.host == "zcode" and change != "unchanged":
        print("note: ZCode asks you to trust new workspace/user hooks on first run "
              "(workspace_hooks_execute_code); approve the jev-kernel hook there.")
    return 0


def _main_plugin(args) -> int:
    """Native plugin package route (zcode pilot): the deploy dir becomes the
    plugin root; registration goes through the plugin store config, and any
    legacy manual hook entry of ours is removed so the call is judged once."""
    cli_config_path(args.host)  # raises for hosts not yet wired
    if args.uninstall:
        changed = unregister_inline_plugin(args.host)
        path = host_config_path(args.host)
        doc = load_doc(path, args.host)
        if uninstall(doc):
            _write(path, doc)
            changed = True
        if not changed:
            print(f"jev-kernel: {args.host} plugin uninstall: nothing to do")
            return 0
        print(f"jev-kernel: {args.host} plugin unregistered "
              f"(removed from {cli_config_path(args.host)} plugins.dirs/enabledPlugins)")
        print(f"note: {deploy_dir(args.host)} kept (it holds the ledger); delete it by hand if unwanted")
        return 0

    if args.dry_run:
        print(json.dumps({"deploy": str(deploy_dir(args.host)),
                          "manifest": PLUGIN_MANIFEST_DIR[args.host] + "/plugin.json",
                          "hooks": "hooks/hooks.json",
                          "command": hook_command(args.host)}, indent=2))
        print(f"jev-kernel: dry run; would deploy plugin to {deploy_dir(args.host)} "
              f"and register in {cli_config_path(args.host)}")
        return 0

    count = deploy_code(args.host)
    seeded = seed_config(args.host)
    write_plugin_package(args.host, args.timeout)
    registered = register_inline_plugin(args.host)

    # the manual hook entry and the plugin would both fire — drop ours
    path = host_config_path(args.host)
    removed_manual = False
    if path.exists():
        doc = load_doc(path, args.host)
        if uninstall(doc):
            _write(path, doc)
            removed_manual = True

    print(f"jev-kernel: {args.host}: plugin package at {deploy_dir(args.host)} "
          f"({count} backend files + {PLUGIN_MANIFEST_DIR[args.host]}/plugin.json + hooks/hooks.json), "
          f"registered in {cli_config_path(args.host)}"
          f"{'' if registered else ' (already registered)'}"
          f"{', legacy hook entry removed' if removed_manual else ''}")
    if seeded:
        print(f"jev-kernel: config {seeded}")
    print("note: restart the host if it does not pick up plugins.dirs live; "
          "approve the trust prompt (plugin hooks execute code) on first run.")
    return 0


def _write(path: Path, doc: dict) -> None:
    backup = path.with_suffix(path.suffix + ".jev-bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
