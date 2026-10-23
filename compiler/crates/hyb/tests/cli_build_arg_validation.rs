//! Fail-to-Pass integration tests for `hyb build` CLI validation and `--deny-managed`.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT: AtomicUsize = AtomicUsize::new(0);

const CORPUS02_MANAGED: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../tests/checker-corpus/02-shared-map-managed.hy"
);

struct TempWorkdir(PathBuf);

impl TempWorkdir {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!(
            "hyb-build-arg-val-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        fs::write(dir.join("a.hy"), "fn main() {}\n").unwrap();
        fs::write(dir.join("b.hy"), "fn main() {}\n").unwrap();
        Self(dir)
    }

    fn build(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_hyb"))
            .arg("build")
            .args(args)
            .env("HYB_ALLOW_TOOLCHAIN_DRIFT", "1")
            .current_dir(&self.0)
            .output()
            .expect("spawn hyb build")
    }
}

impl Drop for TempWorkdir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn assert_exit_2(out: Output, expected_stderr: &str) {
    assert_eq!(
        out.status.code(),
        Some(2),
        "expected exit 2, stdout={}, stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains(expected_stderr),
        "missing {expected_stderr:?} in stderr: {stderr}"
    );
}

#[test]
fn build_deny_managed_rejects_managed_program_and_passes_clean_program() {
    let work = TempWorkdir::new();
    let bad = work.build(&["--deny-managed", "--emit-go", "-o", "Denied", CORPUS02_MANAGED]);
    assert_eq!(bad.status.code(), Some(1), "expected exit 1: {bad:?}");
    let stderr = String::from_utf8_lossy(&bad.stderr);
    assert!(stderr.contains("deny-managed"), "stderr must mention deny-managed: {stderr}");
    assert!(!work.0.join("Denied.go").exists(), "must not emit Go when --deny-managed fails");

    let ok = work.build(&["--deny-managed", "--emit-go", "-o", "Clean", "a.hy"]);
    assert_eq!(ok.status.code(), Some(0), "clean build with --deny-managed must pass: {ok:?}");
    assert!(work.0.join("Clean.go").exists(), "expected emitted Go file");
}

#[test]
fn build_rejects_unknown_flags_missing_values_and_multiple_inputs() {
    let work = TempWorkdir::new();
    assert_exit_2(work.build(&["--unknown-flag", "a.hy"]), "unknown option: --unknown-flag");
    assert_exit_2(work.build(&["a.hy", "-o"]), "-o requires a value");
    assert_exit_2(work.build(&["a.hy", "-o", "--emit-go"]), "-o requires a value");
    assert_exit_2(work.build(&["a.hy", "-o", "x", "-o", "y"]), "-o may only be supplied once");
    assert_exit_2(work.build(&["a.hy", "b.hy"]), "expected exactly one input file");
    assert_exit_2(work.build(&["--emit-go"]), "usage: hyb build");
}

#[test]
fn build_rejects_half_configured_package_or_out_dir_flags() {
    let work = TempWorkdir::new();
    assert_exit_2(
        work.build(&["a.hy", "--package", "foo"]),
        "--package and --out-dir must be used together",
    );
    assert_exit_2(
        work.build(&["a.hy", "--out-dir", "/tmp"]),
        "--package and --out-dir must be used together",
    );
}
