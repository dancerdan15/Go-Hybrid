//! Integration tests for `compiler/crates/hyb/build.rs` pin normalization,
//! Cargo rerun directives, and fail-open `rustc --version` probing.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT: AtomicUsize = AtomicUsize::new(0);
const BUILD_RS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/build.rs");
const FAKE_RUSTC_SRC: &str = r#"
fn main() {
    if let Ok(out) = std::env::var("FAKE_RUSTC_STDOUT") { print!("{out}"); }
    if let Ok(err) = std::env::var("FAKE_RUSTC_STDERR") { eprint!("{err}"); }
    let code: i32 = std::env::var("FAKE_RUSTC_EXIT").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    std::process::exit(code);
}
"#;

struct Harness {
    root: PathBuf,
    build_bin: PathBuf,
    fake_rustc_bin: PathBuf,
}

impl Harness {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "hyb-build-pin-guard-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&root).expect("create temp test dir");
        let suffix = std::env::consts::EXE_SUFFIX;
        let build_bin = root.join(format!("hyb_build_script{suffix}"));
        let fake_rustc_src = root.join("fake_rustc.rs");
        let fake_rustc_bin = root.join(format!("fake_rustc{suffix}"));
        fs::write(&fake_rustc_src, FAKE_RUSTC_SRC).expect("write fake_rustc.rs");

        let rustc = std::env::var("RUSTC")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| "rustc".to_string());
        for (src, bin) in [(BUILD_RS, &build_bin), (fake_rustc_src.to_str().unwrap(), &fake_rustc_bin)] {
            let status = Command::new(&rustc).arg(src).arg("-o").arg(bin).status().expect("compile helper");
            assert!(status.success(), "compiling {src} must succeed");
        }
        Self { root, build_bin, fake_rustc_bin }
    }

    fn run(&self, env_vars: &[(&str, &str)], rustc_stdout: &str, rustc_exit: i32) -> Output {
        let mut cmd = Command::new(&self.build_bin);
        cmd.env_remove("HYB_PINNED_RUST")
            .env_remove("HYB_PINNED_GO")
            .env_remove("HYB_ALLOW_TOOLCHAIN_DRIFT")
            .env("RUSTC", &self.fake_rustc_bin)
            .env("FAKE_RUSTC_STDOUT", rustc_stdout)
            .env("FAKE_RUSTC_EXIT", rustc_exit.to_string());
        for (k, v) in env_vars {
            cmd.env(k, v);
        }
        cmd.output().expect("execute compiled build.rs")
    }
}

impl Drop for Harness {
    fn drop(&mut self) { let _ = fs::remove_dir_all(&self.root); }
}

#[test]
fn emits_rerun_directives_and_passes_when_rustc_matches_default_pin() {
    let h = Harness::new();
    let out = h.run(&[], "rustc 1.99.0 (abcdef123 2026-03-01)\n", 0);
    assert!(out.status.success(), "stderr: {}", String::from_utf8_lossy(&out.stderr));
    let stdout = String::from_utf8_lossy(&out.stdout);
    for expected in [
        "cargo:rerun-if-env-changed=HYB_PINNED_RUST",
        "cargo:rerun-if-env-changed=HYB_PINNED_GO",
        "cargo:rerun-if-env-changed=HYB_ALLOW_TOOLCHAIN_DRIFT",
        "cargo:rerun-if-env-changed=RUSTC",
        "cargo:rerun-if-changed=build.rs",
        "cargo:rustc-env=HYB_PINNED_RUST=1.99.0",
    ] {
        assert!(
            stdout.lines().any(|line| line == expected),
            "missing `{expected}` in stdout:\n{stdout}"
        );
    }
}

#[test]
fn empty_or_whitespace_pinned_rust_falls_back_to_default_pin_and_trims() {
    let h = Harness::new();
    for raw in ["", "   ", "\t\n "] {
        let out = h.run(&[("HYB_PINNED_RUST", raw)], "rustc 1.99.0 (abcdef123 2026-03-01)\n", 0);
        assert!(
            out.status.success(),
            "empty/whitespace HYB_PINNED_RUST={raw:?} must fall back to DEFAULT_PIN, stderr: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(
            stdout.lines().any(|line| line == "cargo:rustc-env=HYB_PINNED_RUST=1.99.0"),
            "stdout must bake normalized default pin:\n{stdout}"
        );
    }

    let out = h.run(&[("HYB_PINNED_RUST", "  1.97.1 \n")], "rustc 1.97.1 (custom 2026-01-15)\n", 0);
    assert!(out.status.success(), "stderr: {}", String::from_utf8_lossy(&out.stderr));
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.lines().any(|line| line == "cargo:rustc-env=HYB_PINNED_RUST=1.97.1"),
        "stdout must bake trimmed override pin:\n{stdout}"
    );
}

#[test]
fn skips_guard_when_rustc_probe_fails_or_emits_non_version_output() {
    let h = Harness::new();
    for (stdout, exit_code) in [
        ("error: rustup could not choose a version of rustc to run\n", 1),
        ("rustc 1.85.0 (failing wrapper)\n", 1),
        ("rustc error: no default toolchain configured\n", 0),
        ("custom-wrapper-banner without rustc prefix\n", 0),
        ("", 0),
    ] {
        let out = h.run(&[], stdout, exit_code);
        assert!(
            out.status.success(),
            "probe ({stdout:?}, exit {exit_code}) must skip the guard, stderr: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    let missing = h.root.join("nonexistent-rustc-binary");
    let out = h.run(&[("RUSTC", missing.to_str().unwrap())], "rustc 1.85.0\n", 0);
    assert!(
        out.status.success(),
        "missing RUSTC binary must skip the guard, stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
}
