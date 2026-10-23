#!/usr/bin/env python3
"""Exercise install.sh's generated launcher against an offline release fixture.

The fixture executable only reports its environment and argv. These tests cover
installer/launcher behaviour, not compiler or Rust bridge correctness. No network
access, toolchain download, account, or third-party Python package is required.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("sh")
if os.name == "nt":
    _git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    SHELL = str(_git_bash) if _git_bash.exists() else None


def shell_path(path):
    path = Path(path).resolve()
    if os.name == "nt":
        return "/" + path.drive[0].lower() + path.as_posix()[2:]
    return str(path)
INSTALL_PATH_NAMES = (
    "ordinary",
    "with spaces",
    "$literal",
    "single'quote",
    'double"quote',
    r"back\slash",
    r"back\$literal",
    r"double\\$literal",
    "trailing\\",
    "literal`printf backtick`",
    "literal$(printf substitution)",
    "trailing-newline\n",
    "combined $literal 'single' \"double\" \\ end\\",
)
PROBE_ARGS = ["build", "file with spaces.hy", "", "$literal", "a'b", 'a"b', r"a\b", "end\\", "line\nbreak"]


class LauncherRuntimeTests(unittest.TestCase):
    def setUp(self):
        supported_os = {"Linux": "linux", "Darwin": "darwin"}
        supported_arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
        if platform.system() not in supported_os or platform.machine() not in supported_arch:
            self.skipTest("install.sh supports Linux/macOS on amd64/arm64")
        required_tools = ("sh", "uname", "mktemp", "mkdir", "rm", "tar", "gzip", "cp", "chmod", "cat", "sed", "head")
        system_tools = {
            name: shutil.which(name, path=os.defpath)
            for name in (*required_tools, "sha256sum", "shasum")
        }
        if any(not system_tools[name] for name in required_tools):
            self.skipTest("the installer requires standard POSIX tools and gzip")
        if not system_tools["sha256sum"] and not system_tools["shasum"]:
            self.skipTest("the installer requires sha256sum or shasum")

        temporary = tempfile.TemporaryDirectory(prefix="hybrid-installer-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / "installed hybrid"
        self.bin_dir = self.root / "launcher bin"
        release = self.root / "release"
        release.mkdir()
        payload = self.root / "hybrid-v0.0.0-test"
        (payload / "runtime").mkdir(parents=True)
        (payload / "runtime" / "fixture.txt").write_text("offline test runtime\n", encoding="utf-8")
        (payload / "hyb").write_text(
            f"#!{sys.executable} -IBS\n"
            "import json, os, sys\n"
            "if sys.argv[1:] == ['explain', 'E0002']:\n"
            "    sys.exit(0)\n"
            "print(json.dumps({'runtime': os.environ.get('HYB_RUNTIME_DIR'), "
            "'executable': os.path.realpath(__file__), 'args': sys.argv[1:]}))\n",
            encoding="utf-8",
        )
        (payload / "hyb").chmod(0o755)
        asset = (
            "hybrid-v0.0.0-test-"
            f"{supported_os[platform.system()]}-{supported_arch[platform.machine()]}.tar.gz"
        )
        with tarfile.open(release / asset, "w:gz") as archive:
            archive.add(payload, arcname=payload.name)
        digest = hashlib.sha256((release / asset).read_bytes()).hexdigest()
        (release / f"{asset}.sha256").write_text(f"{digest}  {asset}\n", encoding="utf-8")

        shim_dir = self.root / "offline tools"
        shim_dir.mkdir()
        # Expose only the installer's local tools, never a real network client.
        for name, path in system_tools.items():
            if path:
                (shim_dir / name).symlink_to(path)
        (shim_dir / "curl").write_text(
            f"#!{sys.executable} -IBS\n"
            "import os, pathlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            "prefix = 'https://github.com/limitlessprojects/Go-Hybrid/releases/download/v0.0.0-test/'\n"
            "if pathlib.Path(sys.argv[0]).name == 'curl' and len(args) == 4 and args[0] == '-fsSL' and args[2] == '-o':\n"
            "    url, output = args[1], args[3]\n"
            "elif pathlib.Path(sys.argv[0]).name == 'wget' and len(args) == 3 and args[0] == '-qO':\n"
            "    url, output = args[2], args[1]\n"
            "else:\n"
            "    sys.exit('unexpected download request in offline test')\n"
            "if not url.startswith(prefix):\n"
            "    sys.exit('unexpected download request in offline test')\n"
            "name = url[len(prefix):]\n"
            "if name != pathlib.Path(name).name:\n"
            "    sys.exit('unexpected asset path in offline test')\n"
            "shutil.copyfile(pathlib.Path(os.environ['HYB_TEST_RELEASE_DIR']) / name, output)\n",
            encoding="utf-8",
        )
        (shim_dir / "curl").chmod(0o755)
        (shim_dir / "wget").symlink_to("curl")
        (self.root / "home").mkdir()
        (self.root / "tmp").mkdir()
        # A fresh environment excludes credentials, shell hooks, and user paths.
        self.env = {
            "HOME": str(self.root / "home"),
            "TMPDIR": str(self.root / "tmp"),
            "PATH": str(shim_dir),
            "LC_ALL": "C",
            "HYB_VERSION": "v0.0.0-test",
            "HYB_INSTALL_DIR": str(self.install),
            "HYB_BIN_DIR": str(self.bin_dir),
            "HYB_TEST_RELEASE_DIR": str(release),
        }

    def install_fixture(self, **overrides):
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh")],
            env={**self.env, **overrides},
            cwd=self.root,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("==> sha256 verified", result.stdout)

    def probe(self, *args, cwd=None, **overrides):
        env = dict(self.env)
        # Exercise the baked-in installation location, as a fresh shell does.
        env.pop("HYB_INSTALL_DIR", None)
        env.update(overrides)
        result = subprocess.run(
            [str(self.bin_dir / "hyb"), *args], env=env,
            cwd=self.root if cwd is None else cwd,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_unset_runtime_uses_packaged_default(self):
        self.install_fixture()
        self.assertEqual(self.probe()["runtime"], str(self.install / "runtime"))

    def test_empty_runtime_uses_packaged_default(self):
        self.install_fixture()
        self.assertEqual(self.probe(HYB_RUNTIME_DIR="")["runtime"], str(self.install / "runtime"))

    def test_relative_install_paths_work_from_another_directory(self):
        for name in ("relative install", "$literal 'quoted'", "trailing-newline\n"):
            with self.subTest(install=name):
                self.install_fixture(HYB_INSTALL_DIR=name)
                result = self.probe(*PROBE_ARGS, cwd=self.root / "home")
                installed = (self.root / name).resolve()
                self.assertTrue(Path(result["runtime"]).is_absolute())
                self.assertEqual(Path(result["runtime"]).resolve(), installed / "runtime")
                self.assertEqual(result["executable"], str(installed / "bin" / "hyb"))
                self.assertEqual(result["args"], PROBE_ARGS)

    def test_relative_install_preserves_runtime_and_install_overrides(self):
        self.install_fixture(HYB_INSTALL_DIR="relative install")
        relocated = self.root / "relocated hybrid"
        shutil.copytree(self.root / "relative install", relocated)
        runtime = str(self.root / "custom runtime $literal 'quoted'")
        result = self.probe(
            *PROBE_ARGS, cwd=self.root / "home",
            HYB_INSTALL_DIR=str(relocated), HYB_RUNTIME_DIR=runtime,
        )
        self.assertEqual(result["runtime"], runtime)
        self.assertEqual(result["executable"], str((relocated / "bin" / "hyb").resolve()))
        self.assertEqual(result["args"], PROBE_ARGS)

    def test_explicit_runtime_is_preserved_verbatim(self):
        self.install_fixture()
        runtime = str(self.root / "custom runtime $literal 'quoted'")
        Path(runtime).mkdir()
        self.assertEqual(self.probe(HYB_RUNTIME_DIR=runtime)["runtime"], runtime)

    def test_runtime_can_change_between_invocations(self):
        self.install_fixture()
        for name in ("runtime one", "runtime two"):
            runtime = str(self.root / name)
            Path(runtime).mkdir()
            with self.subTest(runtime=name):
                self.assertEqual(self.probe(HYB_RUNTIME_DIR=runtime)["runtime"], runtime)

    def test_install_dir_override_sets_default_runtime(self):
        self.install_fixture()
        relocated = self.root / "relocated hybrid"
        shutil.copytree(self.install, relocated)
        self.assertEqual(
            self.probe(HYB_INSTALL_DIR=str(relocated))["runtime"],
            str(relocated / "runtime"),
        )

    def test_explicit_runtime_wins_over_install_dir_override(self):
        self.install_fixture()
        relocated = self.root / "relocated hybrid"
        shutil.copytree(self.install, relocated)
        runtime = str(self.root / "separate runtime")
        Path(runtime).mkdir()
        self.assertEqual(
            self.probe(HYB_INSTALL_DIR=str(relocated), HYB_RUNTIME_DIR=runtime)["runtime"],
            runtime,
        )

    def test_install_time_runtime_is_not_baked_into_launcher(self):
        self.install_fixture(HYB_RUNTIME_DIR=str(self.root / "install-time runtime"))
        self.assertEqual(self.probe()["runtime"], str(self.install / "runtime"))

    def test_arguments_are_forwarded_unchanged(self):
        self.install_fixture()
        args = ["build", "file with spaces.hy", "", "$literal", "a'b"]
        self.assertEqual(self.probe(*args)["args"], args)

    def test_install_paths_preserve_runtime_executable_and_arguments(self):
        for name in INSTALL_PATH_NAMES:
            with self.subTest(install=name):
                installed = self.root / name
                self.install_fixture(HYB_INSTALL_DIR=str(installed))
                expected = {
                    "runtime": str(installed / "runtime"),
                    "executable": str((installed / "bin" / "hyb").resolve()),
                    "args": PROBE_ARGS,
                }
                self.assertEqual(self.probe(*PROBE_ARGS), expected)
                runtime = str(self.root / "runtime overrides" / name)
                self.assertEqual(
                    self.probe(*PROBE_ARGS, HYB_RUNTIME_DIR=runtime),
                    {**expected, "runtime": runtime},
                )

    def test_special_install_overrides_preserve_runtime_precedence(self):
        self.install_fixture()
        for name in INSTALL_PATH_NAMES:
            with self.subTest(install_override=name):
                relocated = self.root / "relocated" / name
                shutil.copytree(self.install, relocated)
                expected = {
                    "runtime": str(relocated / "runtime"),
                    "executable": str((relocated / "bin" / "hyb").resolve()),
                    "args": PROBE_ARGS,
                }
                self.assertEqual(
                    self.probe(*PROBE_ARGS, HYB_INSTALL_DIR=str(relocated)), expected,
                )
                runtime = str(self.root / "runtime overrides" / name)
                self.assertEqual(
                    self.probe(*PROBE_ARGS, HYB_INSTALL_DIR=str(relocated), HYB_RUNTIME_DIR=runtime),
                    {**expected, "runtime": runtime},
                )


    def test_unsafe_hyb_version_is_rejected_before_network(self):
        for bad in ("../evil", "-rf", "..", "v1/2"):
            r = subprocess.run(["sh", str(ROOT / "install.sh")], env={**os.environ, "HYB_VERSION": bad}, capture_output=True, text=True, timeout=5)
            self.assertEqual(r.returncode, 2)


@unittest.skipUnless(SHELL, "requires a POSIX shell (Git Bash works on Windows)")
class RelativePathShellTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="relpath-test-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        shims = self.root / "offline-tools"
        shims.mkdir()
        self.asset = "hybrid-v0.0.0-test-linux-amd64.tar.gz"
        archive_path = self.root / self.asset
        payload_hyb = (
            b'#!/bin/sh\n'
            b'[ "${1:-}" = "explain" ] && [ "${2:-}" = "E0002" ] && exit 0\n'
            b'printf "RUNTIME=%s\\nEXEC=%s\\nARGS=%s\\n" "${HYB_RUNTIME_DIR:-}" "$0" "$*"\n'
        )
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, contents, mode in (
                ("hyb", payload_hyb, 0o755),
                ("runtime/fixture.txt", b"offline runtime\n", 0o644),
            ):
                info = tarfile.TarInfo("hybrid-v0.0.0-test/" + name)
                info.size, info.mode = len(contents), mode
                archive.addfile(info, io.BytesIO(contents))
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        checksum = self.root / (self.asset + ".sha256")
        checksum.write_text(f"{digest}  {self.asset}\n", encoding="utf-8", newline="\n")
        (shims / "uname").write_text(
            '#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; *) exit 2;; esac\n',
            encoding="utf-8", newline="\n",
        )
        (shims / "curl").write_text(
            '#!/bin/sh\n'
            '[ "$#" -eq 4 ] && [ "$1" = "-fsSL" ] && [ "$3" = "-o" ] || exit 2\n'
            'prefix="https://github.com/limitlessprojects/Go-Hybrid/releases/download/v0.0.0-test/"\n'
            'case "$2" in\n'
            '  "${prefix}${HYB_TEST_ASSET}") cp "$HYB_TEST_ARCHIVE" "$4";;\n'
            '  "${prefix}${HYB_TEST_ASSET}.sha256") cp "$HYB_TEST_CHECKSUM" "$4";;\n'
            '  *) exit 2;;\n'
            'esac\n',
            encoding="utf-8", newline="\n",
        )
        for shim in shims.iterdir():
            shim.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("HYB_")}
        self.env.update({
            "HYB_VERSION": "v0.0.0-test",
            "HYB_TEST_INSTALLER": shell_path(ROOT / "install.sh"),
            "HYB_TEST_SHIMS": shell_path(shims),
            "HYB_TEST_ARCHIVE": shell_path(archive_path),
            "HYB_TEST_CHECKSUM": shell_path(checksum),
            "HYB_TEST_ASSET": self.asset,
            "HYB_TEST_CWD": shell_path(self.root),
        })

    def run_shell(self, script, **overrides):
        return subprocess.run(
            [SHELL, "-c", script],
            env={**self.env, **overrides},
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )

    def test_issue_840_relative_bin_and_install_matrix(self):
        matrix = json.loads((ROOT / "tests/issue_840_relative_bin_dir_matrix.json").read_text(encoding="utf-8"))
        (self.root / "nested/sub").mkdir(parents=True, exist_ok=True)
        root_sh = shell_path(self.root)
        for case in matrix["install_cases"]:
            with self.subTest(case=case["id"]):
                invoke_dir = self.root / case["invoke_cwd"]
                invoke_dir.mkdir(parents=True, exist_ok=True)
                res = self.run_shell(
                    'cd "$HYB_TEST_CWD" && PATH="$HYB_TEST_SHIMS:$PATH" sh "$HYB_TEST_INSTALLER"',
                    HYB_INSTALL_DIR=case["install_dir"], HYB_BIN_DIR=case["bin_dir"],
                )
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                exp_inst = root_sh + case["expected_install_suffix"]
                exp_bin = root_sh + case["expected_bin_suffix"]
                self.assertIn(f"==> installed: {exp_bin}/hyb  (runtime at {exp_inst}/runtime)", res.stdout)
                self.assertIn(f'export PATH="{exp_bin}:$PATH"', res.stdout)
                probe = self.run_shell(
                    'cd "$HYB_INVOKE_CWD" && PATH="$HYB_EXP_BIN:$PATH" hyb build app.hy',
                    HYB_INVOKE_CWD=shell_path(invoke_dir), HYB_EXP_BIN=exp_bin,
                )
                self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
                lines = dict(line.split("=", 1) for line in probe.stdout.strip().splitlines())
                self.assertEqual(lines["RUNTIME"], f"{exp_inst}/runtime")
                self.assertEqual(lines["EXEC"], f"{exp_inst}/bin/hyb")
                self.assertEqual(lines["ARGS"], "build app.hy")

        alias = matrix["alias_reject_case"]
        (self.root / "alias-inst/nested").mkdir(parents=True, exist_ok=True)
        bad = self.run_shell(
            'cd "$HYB_TEST_CWD" && PATH="$HYB_TEST_SHIMS:$PATH" sh "$HYB_TEST_INSTALLER"',
            HYB_INSTALL_DIR=alias["install_dir"], HYB_BIN_DIR=alias["bin_dir"],
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn(alias["expected_error"], bad.stderr)

    def test_issue_840_relative_runtime_install_dir_override_matrix(self):
        matrix = json.loads((ROOT / "tests/issue_840_relative_install_dir_override.json").read_text(encoding="utf-8"))
        res = self.run_shell(
            'cd "$HYB_TEST_CWD" && PATH="$HYB_TEST_SHIMS:$PATH" sh "$HYB_TEST_INSTALLER"',
            HYB_INSTALL_DIR="base-install", HYB_BIN_DIR="base-bin",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        shutil.copytree(self.root / "base-install", self.root / "workspace/relocated-hybrid")
        (self.root / "workspace/sub").mkdir(parents=True, exist_ok=True)
        launcher = shell_path(self.root / "base-bin/hyb")
        root_sh = shell_path(self.root)
        for case in matrix["runtime_override_cases"]:
            with self.subTest(case=case["id"]):
                overrides = {
                    "HYB_CALLER_CWD": shell_path(self.root / case["caller_cwd"]),
                    "HYB_LAUNCHER": launcher,
                    "HYB_INSTALL_DIR": case["hyb_install_dir"],
                }
                if "hyb_runtime_dir" in case:
                    overrides["HYB_RUNTIME_DIR"] = case["hyb_runtime_dir"]
                probe = self.run_shell('cd "$HYB_CALLER_CWD" && "$HYB_LAUNCHER" check demo.hy', **overrides)
                self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
                lines = dict(line.split("=", 1) for line in probe.stdout.strip().splitlines())
                self.assertTrue(lines["RUNTIME"].startswith("/"), lines["RUNTIME"])
                expected = case.get("expected_runtime_exact") or (root_sh + case["expected_runtime_suffix"])
                self.assertEqual(lines["RUNTIME"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
