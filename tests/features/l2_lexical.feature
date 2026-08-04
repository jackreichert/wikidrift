@tool @l2_5
Feature: L2.5 lexical drift
  Researchers need a deterministic description of vocabulary redistribution
  between justified article snapshots, with explicit adequacy and source boundaries.

  Background:
    Given a complete usable article corpus exists

  @implemented @offline
  Rule: Snapshot selection follows authoritative upstream state

    Scenario: Every confirmed event uses its own exact endpoints
      Given fresh L1 state contains multiple confirmed events
      When lexical analysis runs for the article
      Then every event is compared using its exact before and after revisions
      And every result is retained under its exact revision-pair identity
      And one unavailable event does not suppress a completed sibling event

    Scenario: Confirmed event uses exact endpoints
      Given fresh L1 state is confirmed
      When lexical snapshots are selected
      Then the before and after snapshots use exact event revisions
      And the lexical receipt records exact-event mode

    Scenario: Rejected candidate uses candidate-relative evidence only when documented
      Given fresh L1 state is not_confirmed
      And an evaluated coarse candidate is retained for audit
      When a candidate-relative lexical comparison is permitted
      Then its mode and candidate boundaries are explicit
      And it is not presented as confirmed-event evidence

    Scenario: Healthy article uses a documented static or interval comparison
      Given fresh L1 state is healthy
      When lexical analysis runs
      Then snapshot selection follows the documented non-event contract
      And no event-relative wording is used

    Scenario: Unavailable L1 cannot silently select event endpoints
      Given L1 is unavailable or stale
      When lexical analysis needs event-relative snapshots
      Then the comparison is unavailable or explicitly static
      And no stale candidate controls endpoint selection

  @implemented @offline
  Rule: Lexical divergence is deterministic and adequate

    Scenario: Comparable prose yields a divergence score
      Given before and after prose meet token adequacy thresholds
      When normalized token distributions are compared
      Then a bounded lexical divergence score is produced
      And before and after token counts are persisted
      And repeated runs produce the same result

    Scenario: Short or empty prose is insufficient
      Given either side does not meet the minimum comparison adequacy
      When lexical analysis runs
      Then the result is insufficient or unavailable
      And no zero-divergence or no-change claim is emitted

    Scenario: Terms used more and less are traceable
      Given a valid lexical comparison exists
      When distinguishing terms are ranked
      Then positive and negative distribution changes are separately represented
      And each displayed term's score derives from persisted counts
      And deterministic tie breakers order equal scores

    Scenario: Stop-word and readability filters do not alter the primary score
      Given display filters suppress unhelpful terms
      When the artifact is rendered
      Then the primary divergence remains computed from the documented analysis vocabulary
      And display filtering does not rewrite the underlying metric

  @implemented
  Rule: Lexical receipts are reusable only under the same contract

    Scenario Outline: Cached lexical output is stale after a contract change
      Given a lexical artifact exists
      And <contract> differs
      When trust is resolved
      Then the artifact is stale or incompatible

      Examples:
        | contract              |
        | source revision pair  |
        | tokenizer version     |
        | normalization rules   |
        | schema version        |
        | threshold contract    |

    Scenario: A lexical artifact retains its source span
      Given lexical analysis succeeds
      When its artifact is persisted
      Then article identity, source revisions, timestamps, comparison mode, counts, scores, and run metadata are recorded

  @implemented @policy
  Rule: Vocabulary change is not meaning change

    Scenario: Lexical wording remains distributional
      Given lexical divergence is high
      When the result is reported
      Then it says vocabulary distribution changed under the documented comparison
      And it does not claim semantic reversal, bias, factual degradation, or intent

    Scenario: Lexical corroboration does not confirm L1
      Given lexical divergence and a coarse rewrite candidate coexist
      When the article state is resolved
      Then lexical divergence may support review priority
      But only exact L1 evidence can confirm durable content loss
