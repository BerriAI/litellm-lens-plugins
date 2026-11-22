import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from lens_codex import codex, install, state, web
from test_integration import Case

ROOT = Path(__file__).resolve().parent.parent


class PluginTests(Case):
    def test_manifests_agree_and_all_hooks_are_bundled(self):
        portable = json.loads((ROOT / "plugin.json").read_text())
        compatibility = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(portable["name"], compatibility["name"])
        self.assertEqual(portable["version"], compatibility["version"])
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks), state.EVENTS)
        for groups in hooks.values():
            self.assertEqual(groups[0]["hooks"][0]["command"], 'bash "${PLUGIN_ROOT}/scripts/run.sh" capture')

    def test_installing_plugin_does_not_reuse_legacy_recording_consent(self):
        with patch.dict(os.environ, {"LENS_CODEX_PLUGIN_ROOT": str(ROOT)}):
            self.assertFalse(state.capture(self.event("UserPromptSubmit", prompt="Do not send")))
        with state.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)

    def test_only_plugin_captures_after_migration(self):
        state.save_config({**state.config(), "capture_mode": "plugin"})
        self.assertFalse(state.capture(self.event("UserPromptSubmit", prompt="legacy duplicate")))
        with patch.dict(os.environ, {"LENS_CODEX_PLUGIN_ROOT": str(ROOT)}):
            self.assertTrue(state.capture(self.event("UserPromptSubmit", prompt="one copy")))
            state.set_enabled(False)
            self.assertFalse(state.capture(self.event("Stop", last_assistant_message="paused")))

    def test_plugin_trust_rejects_matching_commands_from_other_sources(self):
        path = ROOT / "hooks/hooks.json"
        hook = {"key": "ours", "handlerType": "command", "command": "capture",
                "sourcePath": str(path), "source": "plugin", "currentHash": "hash"}
        others = [{**hook, "key": "user", "source": "user"},
                  {**hook, "key": "different-plugin", "sourcePath": str(self.root / "hooks/hooks.json")},
                  {**hook, "key": "different-command", "command": "other"}]
        result = {"data": [{"hooks": [hook, *others, hook]}]}
        self.assertEqual(codex.own_hooks(result, "capture", self.codex, ROOT), [hook])

    def test_migration_preserves_pause_and_unrelated_hooks(self):
        install.copy_app()
        (self.codex / "hooks.json").write_text(json.dumps({"hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "unrelated"}]}]}}))
        install.install_hooks(self.codex)
        old = json.loads((state.state_dir() / "installation.json").read_text())
        state.set_enabled(False)
        with patch.object(codex, "approve_installed_hooks") as approve:
            install.install_plugin_hooks(self.codex, ROOT)
        self.assertEqual(approve.call_args.kwargs["manifest"]["mode"], "plugin")
        hooks = json.loads((self.codex / "hooks.json").read_text())
        self.assertIn("unrelated", json.dumps(hooks))
        self.assertNotIn(old["command"], json.dumps(hooks))
        self.assertFalse(state.config()["enabled"])
        self.assertEqual(json.loads((state.state_dir() / "installation.json").read_text())["mode"], "plugin")

    def test_failed_trust_keeps_legacy_registration(self):
        install.copy_app()
        install.install_hooks(self.codex)
        old = (state.state_dir() / "installation.json").read_bytes()
        with patch.object(codex, "approve_installed_hooks", side_effect=ValueError("not installed")):
            with self.assertRaisesRegex(ValueError, "not installed"):
                install.install_plugin_hooks(self.codex, ROOT)
        self.assertEqual((state.state_dir() / "installation.json").read_bytes(), old)
        self.assertIn(json.loads(old)["command"], (self.codex / "hooks.json").read_text())

    def test_plugin_configure_does_not_write_user_hooks(self):
        with patch.dict(os.environ, {"LENS_CODEX_PLUGIN_ROOT": str(ROOT)}), \
                patch.object(codex, "approve_installed_hooks"), \
                patch.object(web, "verify"):
            web.configure({"gateway": "https://gateway.example", "api_key": "test-private-key",
                           "agent_name": "my-codex"})
        self.assertFalse((self.codex / "hooks.json").exists())
        self.assertEqual(state.config()["capture_mode"], "plugin")
        self.assertTrue(state.config()["enabled"])

    def test_setup_follows_a_codex_home_that_changed_since_the_last_setup(self):
        state.save_config({**state.config(), "codex_home": str(self.codex / "old")})
        moved = self.codex / "new"
        with patch.dict(os.environ, {"LENS_CODEX_PLUGIN_ROOT": str(ROOT), "CODEX_HOME": str(moved)}), \
                patch.object(codex, "approve_installed_hooks"), patch.object(web, "verify"):
            web.configure({"gateway": "https://gateway.example", "api_key": "test-private-key",
                           "agent_name": "my-codex"})
        self.assertEqual(state.config()["codex_home"], str(moved))
        self.assertEqual(json.loads((state.state_dir() / "installation.json").read_text())["codex_home"], str(moved.resolve()))

    def test_plugin_uninstall_preserves_other_hooks_and_removes_key(self):
        (self.codex / "hooks.json").write_text('{"hooks": {"Stop": []}}')
        state.atomic_json(state.state_dir() / "installation.json",
                          {"mode": "plugin", "codex_home": str(self.codex), "plugin_root": str(ROOT)})
        with patch.object(install.sys, "platform", "freebsd"):
            install.uninstall()
        self.assertEqual((self.codex / "hooks.json").read_text(), '{"hooks": {"Stop": []}}')
        self.assertFalse(state.config()["enabled"])
        self.assertNotIn("api_key", state.config())


class GatewayVerifyTests(Case):
    def error(self, code):
        import urllib.error
        return urllib.error.HTTPError("https://gateway.example", code, "", {}, None)

    def test_gateway_without_receipt_route_is_checked_with_an_empty_export(self):
        from lens_codex import delivery
        with patch.object(delivery, "request", side_effect=[self.error(405), {}]) as request:
            delivery.verify({"gateway": "https://gateway.example", "api_key": "k"})
        self.assertEqual([call.args[1:] for call in request.call_args_list][1],
                         ("/v1/traces", {"resourceSpans": []}))

    def test_gateway_rejecting_the_key_still_fails_setup(self):
        from lens_codex import delivery
        with patch.object(delivery, "request", side_effect=[self.error(405), self.error(401)]):
            with self.assertRaisesRegex(ValueError, "cannot send traces"):
                delivery.verify({"gateway": "https://gateway.example", "api_key": "k"})


class LinuxServiceTests(Case):
    def setUp(self):
        super().setUp()
        self.units = self.root / "config"
        patches = [patch.object(install.sys, "platform", "linux"),
                   patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.units)})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_systemd_user_service_runs_the_helper(self):
        with patch.object(install, "systemctl", return_value=True) as systemctl:
            install.install_service()
        unit = install.unit_path().read_text()
        self.assertIn(str(state.state_dir() / "app/lens.py"), unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertEqual([call.args[0] for call in systemctl.call_args_list],
                         ["show-environment", "daemon-reload", "enable", "restart"])

    def test_without_systemd_the_helper_is_started_detached(self):
        with patch.object(install, "systemctl", return_value=False), \
                patch.object(install.subprocess, "Popen") as popen:
            popen.return_value.pid = 4242
            install.install_service()
        self.assertFalse(install.unit_path().exists())
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual((state.state_dir() / "helper.pid").read_text(), "4242")

    def test_uninstall_removes_the_service(self):
        with patch.object(install, "systemctl", return_value=True) as systemctl:
            install.install_service()
            install.uninstall()
        self.assertFalse(install.unit_path().exists())
        self.assertIn(("disable", "--now", "litellm-lens-codex.service"),
                      [call.args for call in systemctl.call_args_list])

    def test_other_platforms_are_refused(self):
        with patch.object(install.sys, "platform", "win32"):
            with self.assertRaisesRegex(ValueError, "macOS and Linux"):
                install.install_service()
