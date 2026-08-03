@layers @l1 @living-documentation
Feature: Detect and explain durable rewrites
  As a researcher
  I want durable content replacement separated from ordinary edit churn
  So that rewrite candidates are reproducible leads rather than accusations

  Rule: L1 measures persistence-weighted loss across mature snapshots

    Scenario: Score an eligible interval
      Given an article has consecutive mature snapshots
      And established wording is present in the earlier snapshot
      When L1 compares the snapshots
      Then wording that survived more prior snapshots receives more removal weight
      And the result records the percentage and mass of persistence-weighted content lost

    Scenario: Exclude an interval before the article is mature
      Given an interval starts before the article meets the mature-size requirement
      When L1 builds the interval profile
      Then the interval is marked as excluded
      And it is not treated as evidence that no rewrite occurred

    Scenario: Keep transient blanking from becoming a durable rewrite
      Given an article is briefly blanked and then restored
      When persistent snapshots are selected
      Then the short-lived blanking is not treated as the stable article state

  Rule: L1 separates coarse candidates from exact confirmation

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
