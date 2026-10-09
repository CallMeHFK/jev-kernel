"""Unit tests for scripts/install_hook.py (per-host deployment).

Covers: fresh install deploys the backend into the host's dot-folder and
registers the deployed command, idempotency, command-drift upgrade (old
repo-path installs get rewritten), config seeding from a legacy shared
~/.jev-kernel, never overwriting an existing host config, and uninstall
removing only the hook entry while keeping the deployed folder.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "install_hook", REPO / "scripts" / "install_hook.py"
)
install_hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_hook)


class InstallHookTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="jev-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.home_patch = mock.patch.object(Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def run_installer(self, *args):
        return install_hook.main(list(args))

    def hooks_doc(self, host):
        return json.loads(install_hook.host_config_path(host).read_text())

    def our_command(self, host):
        for hook in install_hook._our_hooks(self.hooks_doc(host)):
            return hook["command"]
        return None

    def test_fresh_install_deploys_and_registers(self):
        self.assertEqual(self.run_installer("--host", "zcode"), 0)
        deployed = self.home / ".zcode" / "jev-kernel"
        self.assertTrue((deployed / "backend" / "hook.py").is_file())
        self.assertTrue((deployed / "backend" / "decide.py").is_file())
        cmd = self.our_command("zcode")
        self.assertIn(str(deployed / "backend" / "hook.py"), cmd)
        self.assertIn(f"JEV_KERNEL_DATA_DIR={deployed}", cmd)
        self.assertIn("--host zcode", cmd)
        self.assertNotIn(str(REPO), cmd)  # registered command must not point at the dev repo

    def test_idempotent_second_run(self):
        self.run_installer("--host", "claude")
        first = self.hooks_doc("claude")
        self.assertEqual(self.run_installer("--host", "claude"), 0)
        self.assertEqual(first, self.hooks_doc("claude"))

    def test_upgrade_rewrites_repo_path_command(self):
        config = install_hook.host_config_path("codex")
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "version": 1,
            "hooks": {"PreToolUse": [{"hooks": [{
                "type": "command",
                "command": f"/usr/bin/python3 {REPO}/backend/hook.py --host codex # jev-kernel",
                "timeout": 30,
            }]}]},
        }))
        self.run_installer("--host", "codex")
        cmd = self.our_command("codex")
        self.assertNotIn(str(REPO), cmd)
        self.assertIn(str(self.home / ".codex" / "jev-kernel"), cmd)

    def test_existing_hooks_preserved(self):
        config = install_hook.host_config_path("qoder")
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{
                "type": "command", "command": "node /other/guard.mjs", "timeout": 5,
            }]}]},
        }))
        self.run_installer("--host", "qoder")
        blocks = self.hooks_doc("qoder")["hooks"]["PreToolUse"]
        commands = [h["command"] for b in blocks for h in b["hooks"]]
        self.assertIn("node /other/guard.mjs", commands)
        self.assertTrue(any("jev-kernel" in c for c in commands))

    def test_config_seeded_from_legacy_shared_home(self):
        legacy = self.home / ".jev-kernel"
        legacy.mkdir()
        (legacy / "jev.json").write_text(json.dumps({"tiers": ["lux"]}))
        self.run_installer("--host", "zcode")
        seeded = json.loads((self.home / ".zcode" / "jev-kernel" / "jev.json").read_text())
        self.assertEqual(seeded["tiers"], ["lux"])

    def test_existing_host_config_never_overwritten(self):
        self.run_installer("--host", "claude")
        target = self.home / ".claude" / "jev-kernel" / "jev.json"
        target.write_text(json.dumps({"tiers": ["mine"]}))
        (self.home / ".jev-kernel").mkdir()
        (self.home / ".jev-kernel" / "jev.json").write_text(json.dumps({"tiers": ["lux"]}))
        self.run_installer("--host", "claude")
        self.assertEqual(json.loads(target.read_text())["tiers"], ["mine"])

    def test_uninstall_removes_entry_keeps_deploy(self):
        self.run_installer("--host", "zcode")
        self.run_installer("--host", "zcode", "--uninstall")
        self.assertIsNone(self.our_command("zcode"))
        self.assertTrue((self.home / ".zcode" / "jev-kernel" / "backend" / "hook.py").is_file())

    def test_qoder_prefers_cn_dir_when_both_exist(self):
        (self.home / ".qoder").mkdir()
        (self.home / ".qoder-cn").mkdir()
        self.assertEqual(install_hook.host_home("qoder"), self.home / ".qoder-cn")

    # -- native plugin format (zcode pilot) ----------------------------------

    def test_plugin_format_package_and_registration(self):
        self.assertEqual(self.run_installer("--host", "zcode", "--format", "plugin"), 0)
        root = self.home / ".zcode" / "jev-kernel"
        manifest = json.loads((root / ".zcode-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "jev-kernel")
        hooks = json.loads((root / "hooks" / "hooks.json").read_text())
        cmd = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn(str(root / "backend" / "hook.py"), cmd)
        cli_cfg = json.loads((self.home / ".zcode" / "cli" / "config.json").read_text())
        self.assertIn(str(root), cli_cfg["plugins"]["dirs"])
        self.assertTrue(cli_cfg["plugins"]["enabledPlugins"]["jev-kernel@inline"])
        # a plugin must not also sit in the legacy hooks.json (double fire)
        legacy = self.home / ".zcode" / "hooks.json"
        if legacy.exists():
            self.assertEqual(install_hook._our_hooks(json.loads(legacy.read_text())), [])

    def test_plugin_format_removes_legacy_hook_entry(self):
        self.run_installer("--host", "zcode")  # legacy route first
        self.assertIsNotNone(self.our_command("zcode"))
        self.run_installer("--host", "zcode", "--format", "plugin")
        self.assertIsNone(self.our_command("zcode"))
        cli_cfg = json.loads((self.home / ".zcode" / "cli" / "config.json").read_text())
        self.assertTrue(cli_cfg["plugins"]["enabledPlugins"]["jev-kernel@inline"])

    def test_plugin_format_preserves_existing_cli_config(self):
        cfg = self.home / ".zcode" / "cli" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcp": {"servers": {"x": {}}},
                                   "plugins": {"enabledPlugins": {"other@mp": True}}}))
        self.run_installer("--host", "zcode", "--format", "plugin")
        doc = json.loads(cfg.read_text())
        self.assertIn("x", doc["mcp"]["servers"])
        self.assertTrue(doc["plugins"]["enabledPlugins"]["other@mp"])
        self.assertTrue(doc["plugins"]["enabledPlugins"]["jev-kernel@inline"])
        self.assertTrue((self.home / ".zcode" / "cli" / "config.json.jev-bak").exists())

    def test_plugin_uninstall_reverses_registration(self):
        self.run_installer("--host", "zcode", "--format", "plugin")
        self.run_installer("--host", "zcode", "--format", "plugin", "--uninstall")
        doc = json.loads((self.home / ".zcode" / "cli" / "config.json").read_text())
        self.assertNotIn(str(self.home / ".zcode" / "jev-kernel"),
                         doc["plugins"].get("dirs", []))
        self.assertNotIn("jev-kernel@inline",
                         doc["plugins"].get("enabledPlugins", {}))
        self.assertTrue((self.home / ".zcode" / "jev-kernel" / "backend" / "hook.py").is_file())

    def test_plugin_format_rejects_unverified_hosts(self):
        with self.assertRaises(SystemExit):
            self.run_installer("--host", "claude", "--format", "plugin")


if __name__ == "__main__":
    unittest.main()
