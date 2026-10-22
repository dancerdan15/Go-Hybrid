// EXPECTED-VERDICT: REJECT
// HYB-E4003: untyped nil local declaration (rule N, tier0 N1)
package main

fn main() {
    var ch = nil
    _ = ch
}
