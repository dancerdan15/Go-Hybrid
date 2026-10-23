# W-043 (`#585`): Emit Go Syntax Trees, Not Go Source

Executable Wildcard Track spike for [`#585`](https://github.com/limitlessprojects/Go-Hybrid/issues/585) (*Emit Go syntax trees, not Go source: delete the emitter's surface-grammar bug factory*, author `@Leimiu-G`, Prompt #3: *Attack the emit target*).

## Architecture & Invariants

1. **Single-Pass `Paren` Normalization (`normalize_ir`)**: Strips span-only `Paren` wrappers once at the IR entry boundary, collapsing all 8 surface-dispatch bug loci (`#469`, `#593`, `#607`, `#609`, `#618`, `#630`, `#745`) into one structural pass.
2. **Scope-Unique `BindingId` Identity (`BindingId.go_ident`)**: Replaces string-name tracking with SSA-style `BindingId(id, hint, scope_depth, used)`. Unused bindings lower deterministically to `_ = ...` (eliminating Go `"declared and not used"` fixes `8c04d391`/`ab0bb0da`/`2e679d4a`/`58b9d795`), while Go's 25 reserved keywords and shadowed hints deterministically resolve to `hy_<hint>_<id>`.
3. **Construction-Guaranteed Operator Precedence (`materialize`)**: Materializes `GoAstNode` trees against Go's 5 binary precedence tiers (`BIN_PREC`) and non-associative RHS rules (`NON_ASSOC_RHS`), parenthesizing only when required by tree rank and never wrapping `defer`/`go` call targets.
4. **SSA-Dominance `O8` Map-Slot Store-Back (`place_o8_store_backs` + `compute_dominators`)**: Places `m[k] = loan` store-backs by `BindingId` last-use and early-exit dominance rather than scanning remaining text by identifier string (`#547`).
5. **Typed AST Well-Formedness Certificates (`verify_ast`)**:
   - `HYB-E4301`: Rejects unnormalized `Paren` nodes, non-`Call` `defer`/`go` targets, or unhoisted `Try` (`?`) inside expressions.
   - `HYB-E4302`: Rejects unbound `BindingId` references, non-dominated block reads, or reads after `O8` slot-loan flush.

## Falsifier Evaluation on Live `HEAD` (`7d63d5fc`)

| Falsifier | Rule / Threshold | Measured on `HEAD` | Verdict |
| :--- | :--- | :--- | :--- |
| **F1: Classification test** | Surface-notation fixes $\ge 30\%$ of classifiable `hy-emit` fix commits | **19 / 28 (`67.86%`)** surface fixes (`6` scope/position, `12` Go grammar/naming, `1` type spelling) | **PASS** (Thesis holds) |
| **F2: Deletion test** | Re-emit surface-bug shapes via `GoAstNode` IR with $\le 2$ special cases remaining | **8 / 8** `Paren` loci + `#547` shadowed `O8` loan pass with **0** special-case arms | **PASS** (Thesis holds) |
| **F3: Cost / benefit** | Materializer compactness vs `hy-emit` (`7,726` non-test LOC) and corpus drift | **`191` LOC** (`2.5%` of `hy-emit`), removing **29** paren-peel & **79** name-scan sites across **126** corpus fixtures with **0** drift | **PASS** (Thesis holds) |

## Reproducing

```bash
python3 spikes/w043-typed-go-ast-emitter/go_ast_materializer.py --check --json
```
