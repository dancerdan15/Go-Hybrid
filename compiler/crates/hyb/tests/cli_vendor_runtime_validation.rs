//! Fail-to-Pass integration tests for `hyb vendor-runtime` CLI argument validation.

use std::process::{Command, Output};

fn vendor_runtime(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_hyb"))
        .arg("vendor-runtime")
        .args(args)
        .output()
        .expect("spawn hyb vendor-runtime")
}

fn assert_exit_2(out: Output, expected_stderr: &str) {
    assert_eq!(
        out.status.code(),
        Some(2),
        "expected exit 2, stdout={}, stderr={}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.stdout.is_empty(), "expected empty stdout on usage error");
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains(expected_stderr),
        "missing {expected_stderr:?} in stderr: {stderr}"
    );
    assert!(stderr.contains("usage: hyb vendor-runtime"), "{stderr}");
}

#[test]
fn vendor_runtime_rejects_unknown_flags() {
    for flag in ["--bad-flag", "-h", "--unknown"] {
        assert_exit_2(vendor_runtime(&[flag, "dir1"]), "unknown option");
        assert_exit_2(vendor_runtime(&["dir1", flag]), "unknown option");
    }
}

#[test]
fn vendor_runtime_rejects_missing_or_flag_shaped_target_dir_value() {
    assert_exit_2(vendor_runtime(&[]), "usage: hyb vendor-runtime");
    assert_exit_2(vendor_runtime(&["--target-dir"]), "--target-dir requires a value");
    assert_exit_2(
        vendor_runtime(&["--target-dir", "--bad-flag"]),
        "--target-dir requires a value",
    );
}

#[test]
fn vendor_runtime_rejects_extra_positional_or_duplicate_target_dirs() {
    assert_exit_2(
        vendor_runtime(&["dir1", "dir2"]),
        "expected a single target directory",
    );
    assert_exit_2(
        vendor_runtime(&["--target-dir", "dir1", "dir2"]),
        "expected a single target directory",
    );
    assert_exit_2(
        vendor_runtime(&["--target-dir", "dir1", "--target-dir", "dir2"]),
        "expected a single target directory",
    );
}
