//! Integration tests for `HYB_ALLOW_TOOLCHAIN_DRIFT` in `compiler/crates/hyb/build.rs`.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT: AtomicUsize = AtomicUsize::new(0);
const BUILD_RS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/build.rs");
const FAKE_RUSTC_SRC: &str = r#"
fn main() {
    if let Ok(out) = std::env::var("FAKE_RUSTC_STDOUT") { print!("{out}"); }
    let code: i32 = std::env::var("FAKE_RUSTC_EXIT").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    std::process::exit(code);
}
"#;

struct DriftHarness {
    root: PathBuf,
    build_bin: PathBuf,
    fake_rustc_bin: PathBuf,
}

impl DriftHarness {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "hyb-build-drift-override-{}-{}",
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

    fn run(&self, env_vars: &[(&str, &str)], rustc_stdout: &str) -> Output {
        let mut cmd = Command::new(&self.build_bin);
        cmd.env_remove("HYB_PINNED_RUST")
            .env_remove("HYB_PINNED_GO")
            .env_remove("HYB_ALLOW_TOOLCHAIN_DRIFT")
            .env("RUSTC", &self.fake_rustc_bin)
            .env("FAKE_RUSTC_STDOUT", rustc_stdout)
            .env("FAKE_RUSTC_EXIT", "0");
        for (k, v) in env_vars {
            cmd.env(k, v);
        }
        cmd.output().expect("execute compiled build.rs")
    }
}

impl Drop for DriftHarness {
    fn drop(&mut self) { let _ = fs::remove_dir_all(&self.root); }
}

#[test]
fn refuses_mismatched_rustc_by_default_with_diagnostic() {
    let h = DriftHarness::new();
    let out = h.run(&[], "rustc 1.85.0 (4d91de4e4 2025-02-17)\n");
    assert!(!out.status.success(), "mismatched rustc without HYB_ALLOW_TOOLCHAIN_DRIFT=1 must fail");
    let stderr = String::from_utf8_lossy(&out.stderr);
    for needle in [
        "=== HYB pin drift guard (HYB-E9001, build time) ===",
        "rustc `1.85.0` while baking the pin `1.99.0`",
        "HYB_PINNED_RUST=1.85.0 cargo build",
    ] {
        assert!(stderr.contains(needle), "missing `{needle}` in stderr:\n{stderr}");
    }
}

#[test]
fn allows_mismatched_rustc_when_toolchain_drift_override_is_set() {
    let h = DriftHarness::new();
    for val in ["1", " 1 \n"] {
        let out = h.run(&[("HYB_ALLOW_TOOLCHAIN_DRIFT", val)], "rustc 1.85.0 (4d91de4e4 2025-02-17)\n");
        assert!(
            out.status.success(),
            "HYB_ALLOW_TOOLCHAIN_DRIFT={val:?} must bypass compile-time panic, stderr: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(
            stdout.lines().any(|line| line == "cargo:rustc-env=HYB_PINNED_RUST=1.99.0"),
            "drift override must still emit cargo:rustc-env=HYB_PINNED_RUST=1.99.0:\n{stdout}"
        );
    }
}

#[test]
fn non_one_drift_override_values_still_enforce_pin_guard() {
    let h = DriftHarness::new();
    for val in ["", "0", "true", "yes", "2"] {
        let out = h.run(&[("HYB_ALLOW_TOOLCHAIN_DRIFT", val)], "rustc 1.85.0 (4d91de4e4 2025-02-17)\n");
        assert!(
            !out.status.success(),
            "HYB_ALLOW_TOOLCHAIN_DRIFT={val:?} must not bypass the pin guard"
        );
    }
}
