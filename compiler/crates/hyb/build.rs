//! build.rs — the HYB pin drift guard (the adoption-audit follow-up row c).
//!
//! The trap the external reporter hit: `PINNED_RUST` in main.rs is
//! `option_env!("HYB_PINNED_RUST")` — read at COMPILE time. Two silent
//! failure modes:
//!
//!   1. STALE BAKE — cargo does not track env changes: a rebuild after
//!      HYB_PINNED_RUST changed does not recompile, so the binary keeps
//!      enforcing the OLD pin.
//!   2. MISMATCHED BAKE — a plain `cargo build` under a toolchain that is
//!      not the pin bakes the compiled-in default anyway: the resulting
//!      binary enforces a rustc version it was not itself built with.
//!
//! This guard closes both:
//!   * `cargo:rerun-if-env-changed` makes an override change invalidate the
//!     build (no more stale bake);
//!   * a build whose rustc does not match the pin that will be baked is
//!     REFUSED with the three honest ways out — the drift becomes a loud
//!     compile-time event, never a silent rebake.

use std::process::Command;

/// Must match main.rs's `PINNED_RUST` fallback (the default when
/// HYB_PINNED_RUST is unset). check-versions.sh already cross-checks
/// main.rs against tools/env.sh; this constant rides the same discipline.
const DEFAULT_PIN: &str = "1.99.0";

fn resolve_pinned_rust(raw: Option<&str>) -> String {
    raw.map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_PIN)
        .to_string()
}

fn parse_rustc_version(stdout: &str) -> Option<&str> {
    stdout.lines().find_map(|line| {
        let rest = line.trim().strip_prefix("rustc ")?;
        let version = rest.split_whitespace().next()?;
        version
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_digit())
            .then_some(version)
    })
}

fn main() {
    // Fix the stale-bake half: an override change must invalidate the build.
    println!("cargo:rerun-if-env-changed=HYB_PINNED_RUST");
    println!("cargo:rerun-if-env-changed=HYB_PINNED_GO");
    println!("cargo:rerun-if-env-changed=RUSTC");
    println!("cargo:rerun-if-changed=build.rs");

    let pin = resolve_pinned_rust(std::env::var("HYB_PINNED_RUST").ok().as_deref());
    println!("cargo:rustc-env=HYB_PINNED_RUST={pin}");

    // Which rustc is compiling THIS crate? Cargo sets RUSTC for build
    // scripts; fall back to PATH lookup, and if neither yields a version,
    // SKIP the guard — never block a build on the guard itself (the
    // runtime HYB-E9001 check still governs every hyb build).
    let rustc = std::env::var("RUSTC")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "rustc".to_string());
    let Ok(out) = Command::new(&rustc).arg("--version").output() else {
        return;
    };
    if !out.status.success() {
        return;
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let Some(found) = parse_rustc_version(&stdout) else {
        return;
    };
    if found == pin {
        return;
    }

    panic!(
        "\n=== HYB pin drift guard (HYB-E9001, build time) ===\n\
         this `cargo build` compiles hyb with rustc `{found}` while baking the pin `{pin}` —\n\
         the resulting binary would enforce a toolchain it was not built with, and a later\n\
         rebuild with a different HYB_PINNED_RUST would silently rebake (the exact trap the\n\
         external reporter hit: their 1.97.1 override vanished on the next plain build).\n\n\
         three honest ways out:\n\
         \x20 1. build through the pinned env:  source tools/env.sh && cargo build\n\
         \x20    (PATH + HYB_PINNED_RUST move together — the repo's own recipe)\n\
         \x20 2. build with your toolchain AND enforce it deliberately:\n\
         \x20    HYB_PINNED_RUST={found} cargo build\n\
         \x20    (the binary then enforces {found}; record the change in PLAN.md's\n\
         \x20     environment row + README + tools/env.sh in the same commit)\n\
         \x20 3. upgrade your toolchain to the pin:  rustup install {pin} && rustup default {pin}\n"
    );
}
