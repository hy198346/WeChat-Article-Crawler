import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestInstallAnalysisServicesScript(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script_path = self.repo_root / "bin" / "install_analysis_services_launchd.sh"

    def _run_script(self, extra_env=None):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        log_path = temp_dir / "launchctl.log"
        fake_bin = temp_dir / "bin"
        fake_bin.mkdir()
        launchctl_path = fake_bin / "launchctl"
        launchctl_path.write_text(
            "#!/bin/zsh\n"
            "echo \"$@\" >> \"$TEST_LAUNCHCTL_LOG\"\n",
            encoding="utf-8",
        )
        launchctl_path.chmod(0o755)

        env = os.environ.copy()
        env["HOME"] = str(temp_dir / "home")
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        env["TEST_LAUNCHCTL_LOG"] = str(log_path)
        if extra_env:
            env.update(extra_env)

        result = subprocess.run(
            ["zsh", str(self.script_path)],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True,
        )
        calls = []
        if log_path.exists():
            calls = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return result, temp_dir, calls

    def test_install_script_copies_plists_and_bootstraps_all_services_in_order(self):
        result, temp_dir, calls = self._run_script()

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        agents_dir = temp_dir / "home" / "Library" / "LaunchAgents"
        self.assertTrue((self.repo_root / "logs").exists())
        self.assertTrue((self.repo_root / "output").exists())
        self.assertTrue((agents_dir / "com.wechat.articlecrawler.analysis-queue.plist").exists())
        self.assertTrue((agents_dir / "com.wechat.articlecrawler.analysis-static.plist").exists())
        self.assertTrue((agents_dir / "com.wechat.articlecrawler.reanalyze-api.plist").exists())
        uid = os.getuid()
        self.assertEqual(
            calls,
            [
                f"bootout gui/{uid} {agents_dir / 'com.wechat.articlecrawler.analysis-queue.plist'}",
                f"bootstrap gui/{uid} {agents_dir / 'com.wechat.articlecrawler.analysis-queue.plist'}",
                f"bootout gui/{uid} {agents_dir / 'com.wechat.articlecrawler.analysis-static.plist'}",
                f"bootstrap gui/{uid} {agents_dir / 'com.wechat.articlecrawler.analysis-static.plist'}",
                f"bootout gui/{uid} {agents_dir / 'com.wechat.articlecrawler.reanalyze-api.plist'}",
                f"bootstrap gui/{uid} {agents_dir / 'com.wechat.articlecrawler.reanalyze-api.plist'}",
            ],
        )

    def test_install_script_honors_custom_launchd_domain(self):
        result, temp_dir, calls = self._run_script({"WECHAT_LAUNCHD_DOMAIN": "gui/999"})

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        agents_dir = temp_dir / "home" / "Library" / "LaunchAgents"
        self.assertEqual(
            calls,
            [
                f"bootout gui/999 {agents_dir / 'com.wechat.articlecrawler.analysis-queue.plist'}",
                f"bootstrap gui/999 {agents_dir / 'com.wechat.articlecrawler.analysis-queue.plist'}",
                f"bootout gui/999 {agents_dir / 'com.wechat.articlecrawler.analysis-static.plist'}",
                f"bootstrap gui/999 {agents_dir / 'com.wechat.articlecrawler.analysis-static.plist'}",
                f"bootout gui/999 {agents_dir / 'com.wechat.articlecrawler.reanalyze-api.plist'}",
                f"bootstrap gui/999 {agents_dir / 'com.wechat.articlecrawler.reanalyze-api.plist'}",
            ],
        )


if __name__ == "__main__":
    unittest.main()
