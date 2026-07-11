# TokTrack: A Complete Token Provenance and Change Tracking Dataset for the English Wikipedia

| | |
|---|---|
| **Authors** | Fabian Flöck, Kenan Erdogan, Maribel Acosta |
| **Venue** | ICWSM 2017 |
| **arXiv** | https://arxiv.org/abs/1703.08244 |

## What it is

A **dataset release**: every token instance in undeleted, non-redirect English Wikipedia articles through ~October 2016 (~13.5B instances), each annotated with:

1. The revision that **created** the token  
2. All revisions where it was **deleted / re-added / re-deleted**

so survival, conflict, and partial-revert metrics can be computed without re-running expensive text alignment.

## Method (sketch)

Applies a WikiWho-class state-of-the-art tracker at full-corpus scale; publishes the result so researchers skip the hard compute.

## Findings / contribution

Makes token-level provenance, content survival, fine-grained conflict, and partial-revert analysis feasible at Wikipedia scale. Shows novel metrics enabled by complete token histories.

## Relation to WikiDrift

Same intellectual line as WikiWho. Confirms that **deleted-token lifecycle** (not just current blame) is a first-class research object — exactly what L1 uses for “how much of the pre-window spine is gone.” Hosted API + local `wikiwho_rs` are the live implementations WikiDrift calls.
