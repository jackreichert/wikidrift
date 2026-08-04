@layers @l1 @living-documentation
Feature: Detect and explain durable rewrites
  As a researcher
  I want durable content replacement separated from ordinary edit churn
  So that rewrite candidates are reproducible leads rather than accusations

  Rule: L1 measures persistence-weighted change across covered snapshots

    Scenario: Score an eligible interval
      Given an article has consecutive covered snapshots
      And established wording is present in the earlier snapshot
      When L1 compares the snapshots
      Then wording that survived more prior snapshots receives more weight
      And the result records persistence-weighted loss, standing gain, retention, and paired change
      And paired change is labeled a replacement lead rather than a confirmed semantic replacement

    Scenario: Preserve an anomaly below the exact-check floor
      Given a covered interval starts with fewer than 1,000 tokens
      And its loss, standing gain, or paired change crosses the configured sweep floor
      When L1 builds the interval profile
      Then the anomaly remains visible as descriptive evidence
      And it is marked not confirmable rather than excluded
      And it is not treated as evidence that no change occurred

    Scenario Outline: Starting token count determines exact confirmability
      Given a covered interval starts with <tokens> tokens
      When L1 records its evidence state
      Then the interval is marked <confirmability>

      Examples:
        | tokens | confirmability |
        | 999    | not confirmable |
        | 1000   | confirmable     |
        | 1001   | confirmable     |

    Scenario: Use mass to prioritize without suppressing anomalies
      Given a covered interval crosses a configured sweep percentage floor
      When L1 ranks the anomaly for review
      Then persistence-weighted mass may raise its priority
      But low mass does not remove the anomaly from the sweep result
      And priority does not remove a confirmable loss candidate from exact checking

    Scenario: Keep transient blanking from becoming a durable rewrite
      Given an article is briefly blanked and then restored
      When persistent snapshots are selected
      Then the short-lived blanking is not treated as the stable article state

  Rule: L1 separates coarse candidates from exact confirmation

    Scenario: Retain a fourth covered sweep anomaly
      Given four covered intervals cross a configured sweep floor
      When L1 serializes sweep candidates
      Then all four anomalies are retained with their type, priority, and evidence state
      And types are loss, gain, or replacement
      And priorities are high, review, or low
      And unresolved evidence states are pending_confirmation or descriptive_only
      And no candidate is discarded solely because of rank or persistence-weighted mass

    Scenario: Confirm a durable rewrite candidate
      Given a coarse interval crosses the candidate threshold
      And revision-level checking finds that the durable spine dropped by the required amount
      When L1 records the exact result
      Then the candidate is marked confirmed
      And the exact before and after revisions are retained
      And the durable-spine drop and persistence-weighted mass are retained

    Scenario: Reject a coarse candidate
      Given a coarse interval crosses the candidate threshold
      But revision-level checking does not find the required durable-spine drop
      When L1 records the exact result
      Then the candidate is marked rejected with a reason
      And it is not described as a confirmed rewrite

    Scenario: Preserve a completed negative result
      Given L1 completed without confirming any candidate
      When the result is published
      Then the article is marked not confirmed
      And the result does not claim that the article never changed

    Scenario: Do not label unresolved anomalies healthy
      Given one or more sweep anomalies remain descriptive or fail exact confirmation
      When the result is published
      Then the article is labeled descriptive anomalies or anomalies unconfirmed
      And it is not labeled healthy

    Scenario: Preserve an article with only sub-floor anomalies
      Given every sweep anomaly starts below the exact-check token floor
      When L1 records the article result
      Then the article is labeled descriptive anomalies
      And every anomaly remains available for inspection

    Scenario: Preserve unavailable evidence
      Given the snapshot corpus is incomplete or stale
      When L1 cannot publish a current exact decision
      Then the result is marked unavailable
      And it is not silently converted to healthy or not confirmed

  Rule: L1.6 attributes public editing actions without inferring intent

    Scenario: Explain a confirmed event with public revision evidence
      Given L1 has a fresh confirmed event
      And attribution evidence is available
      When L1.6 describes the event
      Then it identifies public accounts associated with removals and standing replacement text
      And it separates gross activity from text that remained at the stable endpoint
      And each displayed action can be traced to public revision evidence

    Scenario: Avoid an unsupported motive claim
      Given an account is associated with a large share of a confirmed rewrite
      When L1.6 publishes attribution
      Then it describes the account's observable editing actions
      And it does not claim bias, coordination, ownership, policy violation, or bad intent

    Scenario: Report unavailable attribution honestly
      Given a rewrite is confirmed
      But exact attribution cannot be produced
      When the event is published
      Then the rewrite remains confirmed
      And attribution is shown as unavailable with a reason
