// EXPECTED-VERDICT: ACCEPT
// P4 positive control: N1 ascribed error = nil and N3 zero-value channel declaration
package main

fn main() {
    var e error = nil
    var ch chan int
    _ = e
    _ = ch
}
