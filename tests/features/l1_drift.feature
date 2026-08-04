@tool @l1
Feature: L1 persistence-weighted drift and exact confirmation
  Researchers need a deterministic detector that preserves durable change leads,
  then confirms or rejects loss candidates against exact revisions before downstream use.

  Background:
    Given a complete usable L0 corpus exists for the canonical article

  @implemented @offline
  Rule: Coarse analysis proposes candidates without deciding them

    Scenario: Stable token persistence is measured before interval loss
      When token persistence is calculated across ordered revisions
      Then each eligible token receives a persistence weight derived from its observed survival
      And transient tokens contribute less than durable tokens
      And calculations are deterministic for the same corpus and thresholds

    Scenario: Interval PWR change is computed from stable content
      Given an eligible pair of interval endpoints
      When interval change is measured
      Then removed, retained, and standing-added persistence-weighted mass are recorded
      And loss, retention, standing gain, and paired-change percentages are recorded
      And interval duration and endpoint revisions are retained

    Scenario: A sub-floor anomaly remains descriptive
      Given a covered interval starts with fewer than 1,000 tokens
      And loss, standing gain, or paired change crosses its configured sweep floor
      When the interval profile is computed
      Then the interval is retained as a descriptive anomaly
      And it is marked not confirmable rather than excluded
      And the evidence state is distinguishable from missing source coverage

    Scenario Outline: Starting token count determines exact confirmability
      Given a covered interval starts with <tokens> tokens
      When the interval profile is computed
      Then its exact evidence state is <confirmability>

      Examples:
        | tokens | confirmability |
        | 999    | not confirmable |
        | 1000   | confirmable     |
        | 1001   | confirmable     |

    Scenario: Threshold crossing creates a candidate
      Given an eligible interval's PWR loss, standing gain, or paired change meets a configured sweep floor
      When coarse candidate generation runs
      Then the interval is retained as a candidate
      And the configured sweep thresholds are persisted with the result
      And the candidate is not labeled confirmed

    Scenario: Persistence-weighted mass changes review order but not admission
      Given two eligible intervals cross the same configured sweep percentage floor
      And one interval has less persistence-weighted mass than the review-priority floor
      When coarse candidate generation runs
      Then both intervals are retained
      And persistence-weighted mass may assign them different review priorities and review order
      And both receive exact checking when they are confirmable loss candidates

    Scenario: Paired loss and gain remain a replacement lead
      Given an interval has concurrent persistence-weighted loss and standing gain
      When paired change crosses the replacement sweep floor
      Then the interval records a replacement lead
      And it is not labeled a confirmed semantic replacement

    Scenario: No threshold crossing is healthy at the coarse layer
      Given every eligible interval is below the configured loss, gain, and paired-change sweep floors
      When coarse analysis completes
      Then the L1 coarse state is healthy
      And the profile still preserves measured eligible intervals

  @implemented @offline
  Rule: Exact revisions decide event confirmation

    Scenario: A fourth confirmable loss candidate is evaluated
      Given four loss candidates cross the exact-check admission contract
      When exact confirmation runs
      Then all four confirmable loss candidates are evaluated
      And no candidate is discarded solely because of rank or persistence-weighted mass

    Scenario Outline: Sweep candidates use stable serialized vocabulary
      Given a sweep candidate has anomaly type <type>
      And its review priority is <priority>
      And its exact confirmability is <confirmability>
      When the candidate receipt is serialized
      Then its unresolved evidence state is <state>

      Examples:
        | type        | priority | confirmability | state                |
        | loss        | high     | confirmable     | pending_confirmation |
        | gain        | review   | confirmable     | pending_confirmation |
        | replacement | low      | not confirmable | descriptive_only     |

    Scenario: Exact checking narrows a coarse window
      Given a coarse candidate spans multiple revisions
      When exact confirmation runs
      Then candidate-relative revision boundaries are evaluated within that window
      And each evaluated candidate retains exact before and after revisions, metrics, and decision

    Scenario: Durable loss confirms an exact event
      Given an evaluated exact boundary meets the durable-spine drop, persistence-mass, and duration contract
      When the decision is made
      Then the event state is confirmed
      And exact before and after revision IDs are authoritative
      And the supporting metric vector and thresholds are persisted

    Scenario: Exact evidence rejects a coarse candidate
      Given no evaluated boundary in a coarse candidate meets the exact contract
      When exact confirmation completes
      Then the candidate decision is rejected
      And the authoritative article state is not_confirmed when no other event confirms
      And the coarse candidate remains available for audit

    Scenario: Gain and replacement leads remain provisional
      Given a standing-gain or replacement lead crosses its sweep floor
      When no dedicated semantic confirmation contract exists for that lead type
      Then the lead remains descriptive or pending
      And it cannot become an exact confirmed rewrite by gain evidence alone

    Scenario: An article with only sub-floor anomalies is not healthy
      Given every sweep anomaly starts below the exact-check token floor
      When L1 records the authoritative article state
      Then the state is descriptive_anomalies
      And every anomaly remains available for inspection

    Scenario: Multiple confirmed events remain separate
      Given more than one non-equivalent exact boundary meets the contract
      When confirmation results are persisted
      Then each event has an independent identity and receipt
      And downstream consumers do not collapse them into one synthetic event

    Scenario: Event ordering is deterministic
      Given multiple candidates or events have equal primary scores
      When results are ordered
      Then stable revision/time tie breakers produce the same order on repeated runs

  @implemented
  Rule: Confirmation receipts carry enough evidence for downstream trust

    Scenario: A current confirmation records its schema and inputs
      Given exact confirmation completes
      When its receipt is written
      Then it records current schema version, run timestamp, corpus horizon, exact thresholds, sweep thresholds, final state, interval profile, sweep candidates, and evaluated candidates
      And confirmed events include exact boundary evidence

    Scenario: Sweep thresholds do not invalidate unchanged exact evidence
      Given a persisted exact confirmation matches the current exact threshold contract and corpus horizon
      But the additive sweep threshold contract has changed
      When downstream trust resolves the exact confirmation
      Then the exact receipt remains current
      And refreshed sweep evidence records its own threshold contract separately

    Scenario: Legacy persisted confirmation is refreshed
      Given a persisted confirmation has a run timestamp but no current schema version
      When the L1 pipeline resolves state
      Then the legacy receipt is stale
      And confirmation is rerun before the result can be authoritative

    Scenario: A synthetic fixture without persistence metadata remains testable
      Given an in-memory minimal fixture has no run timestamp and no schema version
      When a pure unit contract evaluates it
      Then compatibility may be preserved for that fixture
      But the same exception cannot authorize a persisted production receipt

  @implemented
  Rule: The interval profile supports honest visualization

    Scenario: Every covered interval has an explicit profile row
      When the L1 profile is serialized
      Then each covered interval records its endpoints, loss, standing gain, retention, paired change, PWR masses, anomaly types, priority, and evidence state
      And confirmability is recorded separately from source eligibility

    Scenario: Candidate decisions join the profile without rewriting measurements
      Given a profile interval produced an exact candidate evaluation
      When the profile is consumed
      Then the measured coarse loss remains unchanged
      And exact status is attached as confirmation or rejection metadata

  @implemented @offline
  Rule: Benchmark and calibration separate detector quality from anecdotes

    Scenario: Golden verdicts test exact behavior
      Given a labeled benchmark fixture identifies expected healthy, confirmed, rejected, or unavailable outcomes
      When the detector runs with the benchmark contract
      Then actual and expected states are compared
      And mismatches fail validation

    Scenario: Pre-ranking never replaces exact confirmation
      Given a heuristic or M-score ranks articles or windows for review
      When a high score is observed
      Then it may change processing priority
      But it cannot change exact confirmation state

    Scenario: Threshold changes invalidate comparability
      Given two findings use different sweep or exact threshold contracts
      When they are compared or aggregated
      Then the contract difference is disclosed
      And affected measurements are not presented as directly comparable without recalculation
      But an additive sweep change does not stale an otherwise unchanged exact receipt

  @implemented @policy
  Rule: L1 wording stays within its evidentiary authority

    Scenario: Confirmed means durable loss only
      Given an event is confirmed
      When it is described
      Then the wording says that long-stable content was durably removed across exact boundaries
      And it does not claim that the article became better, worse, biased, false, or ideologically captured

    Scenario: A replacement lead is not a semantic verdict
      Given concurrent loss and standing gain produce a replacement lead
      When the lead is described
      Then the wording says that paired change is worth inspection
      And it does not claim that the gained text replaced the lost text in meaning or purpose

    Scenario: Healthy is threshold-relative
      Given coarse analysis is healthy
      When the result is described
      Then it says no candidate was found under the current thresholds and corpus horizon
      And it does not claim that the article never changed materially
