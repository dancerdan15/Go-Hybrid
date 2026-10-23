# Spike W-057: Global Choreography Projection & Deadlock-Free Local `select` State Machines

**Wildcard Proposal:** [#1279](https://github.com/limitlessprojects/Go-Hybrid/issues/1279) (`@dancerdan15`)  
**Target Assumptions:**
- [`GUARANTEES.md` Row 16](../../GUARANTEES.md) — *"Send-on-closed-channel panics / multi-channel protocol liveness: No claim — a liveness/protocol property, out of Tier-0's scope"*
- [`MOONSHOT.md` Active Solicitation #3 & #4](../../MOONSHOT.md) — *Attack the emit target & reach "impossible" deadlock-freedom guarantees in the checked subset*

## Formal Mechanism: Honda–Yoshida–Carbone Endpoint Projection (EPP)

`ChoreographyProjector` compiles a single global protocol type `choreography P(R_1, ..., R_n)` into deadlock-free per-role local `select` state machines ($G \upharpoonright R_i$), enforcing three compile-time gates:
1. **`HYB-E5701` (`UnprojectableChoiceKnowledgeGap`):** Enforces full branch mergeability ($\bigsqcup_k (G_k \upharpoonright C)$). Every third participant $C \neq A$ whose local behavior diverges across `choice at A` must first receive pairwise-disjoint message labels from the same notifying peer before performing distinct actions.
2. **`HYB-E5702` (`CausalSelfCommunicationOrDisconnectedStep`):** Rejects reflexive self-sends (`A -> A: Msg`) and causally disconnected branch steps where a participant transmits before being causally informed of the active choice branch.
3. **`HYB-E5703` (`LocalImplementationDeviatesFromProjection`):** Checks `@role(Protocol::Participant)` goroutines against $G \upharpoonright R_i$ and proves via finite-state LTS exploration **`0` deadlock states**, **`0` orphan messages**, and **`0` send-on-closed-channel panics**.

## Pre-Registered Falsifiers & Results

| Falsifier | Rule / Target | Verdict | Evidence |
|---|---|---|---|
| **F1** | Enforce EPP branch mergeability; reject choice knowledge gaps with `HYB-E5701` | **PASS** | `3_uninvolved_identical_merge_accept`, `4_non_mergeable_knowledge_gap_reject` |
| **F2** | Reject self-communication and causally disconnected choice branches with `HYB-E5702` | **PASS** | `5_reflexive_self_comm_reject`, `6_causally_disconnected_branch_reject` |
| **F3** | Reject local `select` arm omission & post-close sends (`HYB-E5703`); prove `0` deadlocks | **PASS** | `1_two_buyer_epp_accept`, `2_stream_close_row16_accept`, `7`–`8` (`8/8` verified) |

## Reproduce

```bash
python3 spikes/w057-multiparty-session-projections/choreography_projector.py --check
python3 -m unittest discover -s spikes/w057-multiparty-session-projections/tests -p "test_*.py" -v
```
