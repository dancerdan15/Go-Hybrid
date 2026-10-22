// EXPECTED-VERDICT: ACCEPT
// P1 positive control: true scalar const shared across task boundary emits Go const
package main

const limit int = 64

fn worker(n int) int {
    return n + limit
}

fn main() {
    t := task worker(limit)
    _ = await t
}
