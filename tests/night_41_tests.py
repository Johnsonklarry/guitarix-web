import os
import subprocess
import tempfile
import unittest
import textwrap
import shutil

class TestDeployScriptDirtyTreeRace(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory to act as a fake repo
        self.repo_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.repo_dir)

        # Create a minimal deploy.sh copy
        self.deploy_path = os.path.join(self.repo_dir, "deploy.sh")
        shutil.copyfile(
            os.path.join(self.original_cwd, "tools", "deploy.sh"),
            self.deploy_path,
        )
        os.chmod(self.deploy_path, 0o755)

        # Create a fake git executable that simulates the race
        self.fake_git_dir = os.path.join(self.repo_dir, "fake_git")
        os.makedirs(self.fake_git_dir, exist_ok=True)
        self.fake_git_path = os.path.join(self.fake_git_dir, "git")
        with open(self.fake_git_path, "w") as f:
            f.write(textwrap.dedent("""\
                #!/bin/sh
                STATUS_FILE="${TMPDIR:-/tmp}/git_status_stage"
                case "$1" in
                  status)
                    # First call returns clean, second call returns dirty
                    if [ ! -f "$STATUS_FILE" ]; then
                      touch "$STATUS_FILE"
                      echo ""
                    else
                      echo "M  modified_file"
                    fi
                    ;;
                  rev-parse)
                    echo "deadbeef"
                    ;;
                  pull)
                    # simulate successful pull
                    exit 0
                    ;;
                  log|diff|rev-parse|rev-parse|rev-parse)
                    # not needed for this test
                    exit 0
                    ;;
                  *)
                    echo "Unsupported git command: $1" >&2
                    exit 1
                    ;;
                esac
                """))
        os.chmod(self.fake_git_path, 0o755)

        # Prepend fake git to PATH
        self.original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.fake_git_dir + os.pathsep + self.original_path

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.repo_dir, ignore_errors=True)
        os.environ["PATH"] = self.original_path

    def test_deploy_aborts_on_dirty_after_pull(self):
        # Run the deploy script; it should exit with non‑zero status because
        # the second git status reports a dirty working tree.
        result = subprocess.run([self.deploy_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0, msg="Deploy script should abort when repo becomes dirty after pull")

if __name__ == "__main__":
    unittest.main()
