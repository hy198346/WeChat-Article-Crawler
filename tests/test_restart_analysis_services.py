import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestRestartAnalysisServicesScript(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script_path = self.repo_root / "bin" / "restart_analysis_services.sh"

    def _run_script(self, extra_env=None):
        with tempfile.TemporaryDirectory() as d:
            temp_dir = Path(d)
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
            return result, calls

    def test_restart_script_kickstarts_analysis_queue_then_analysis_static_then_reanalyze_api(self):
        result, calls = self._run_script()

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        uid = os.getuid()
        self.assertEqual(
            calls,
            [
                f"kickstart -k gui/{uid}/com.wechat.articlecrawler.analysis-queue",
                f"kickstart -k gui/{uid}/com.wechat.articlecrawler.analysis-static",
                f"kickstart -k gui/{uid}/com.wechat.articlecrawler.reanalyze-api",
            ],
        )

    def test_restart_script_uses_custom_launchd_domain_when_configured(self):
        result, calls = self._run_script({"WECHAT_LAUNCHD_DOMAIN": "system"})

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertEqual(
            calls,
            [
                "kickstart -k system/com.wechat.articlecrawler.analysis-queue",
                "kickstart -k system/com.wechat.articlecrawler.analysis-static",
                "kickstart -k system/com.wechat.articlecrawler.reanalyze-api",
            ],
        )


if __name__ == "__main__":
    unittest.main()
