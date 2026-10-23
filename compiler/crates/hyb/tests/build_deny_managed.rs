//! `hyb build --deny-managed` must apply the same CI policy gate as
//! `hyb check --deny-managed` (see tests/deny_managed.rs): a program with a
//! `managed` block fails (exit 1) before any Go is emitted, while a
//! hatch-free program still builds.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

const CORPUS02_MANAGED: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../tests/checker-corpus/02-shared-map-managed.hy"
);

struct TempWorkdir(PathBuf);

impl TempWorkdir {
    fn new() -> Self {
        let dir = std::env::temp_dir()
            .join(format!("hyb-build-deny-managed-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");
        fs::write(dir.join("a.hy"), "fn main() {}\n").unwrap();
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
