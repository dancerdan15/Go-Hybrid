// EXPECTED-VERDICT: REJECT
// HYB-E3001: composite const lowered to runtime var cannot cross task boundary without move
package main

struct Config {
    limit int
}

const globalCfg Config = Config{limit: 64}

fn worker(c Config) int {
    return c.limit
}

fn main() {
    t := task worker(globalCfg)
    _ = await t
}
