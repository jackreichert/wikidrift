@tool @l1_6
Feature: L1.6 revision attribution and editorial-process context
  Researchers need a reproducible account of observable actions around exact event boundaries
  without inferring identity, motive, ownership, coordination, or policy violation.

  Background:
    Given a fresh exact L1 confirmation exists

  @implemented @offline
  Rule: Attribution is derived from ordered revisions

    Scenario: Removed text is attributed to observed removal actions
      Given an exact before and after boundary contains removed durable text
      When multi-revision attribution runs across the event window
      Then removed token mass is associated with public revision actors according to the documented algorithm
      And each aggregate retains raw count evidence

    Scenario: Surviving replacement text is attributed to origin revisions
      Given text present after the event survives in the evaluated horizon
      When origin attribution runs
      Then the text is associated with its earliest supported origin revision in the analyzed sequence
      And public actor state is retained when available

    Scenario: Registered accounts remain distinct
      Given two different registered account names appear in the history
      When attribution is aggregated
      Then their actions remain separate
      And the tool does not merge them by name similarity or external identity claims

    Scenario Outline: Non-registered identity states are not treated as named people
      Given an action actor is <state>
      When attribution is emitted
      Then the actor remains classified as <state>
      And no person identity is inferred

      Examples:
        | state       |
        | anonymous IP|
        | bot         |
        | hidden      |
        | unavailable |
        | renamed     |

  @implemented
  Rule: Attribution percentages remain recomputable

    Scenario: Displayed shares derive from raw counts
      Given a receipt displays an account share of removed or surviving mass
      When the share is recomputed from the receipt's numerator and denominator
      Then it matches the displayed value within rounding tolerance

    Scenario: Empty denominators produce unavailable shares
      Given no eligible attributed mass exists for a category
      When shares are computed
      Then no division-by-zero or fabricated zero-share claim occurs
      And the share is unavailable or omitted with a reason

    Scenario: Attribution order is deterministic
      Given equal attributed masses occur
      When actors are ranked for display
      Then stable literal actor and revision tie breakers determine order

  @implemented @offline
  Rule: Editorial-process context is corroborative only

    Scenario: Revision actions are counted around an exact event
      Given the event window contains public revision metadata
      When process context is computed
      Then edits, reverts, and other supported action classes are summarized with revision links
      And observed and not-observed states remain distinct

    Scenario: Talk-page context is bounded to observable records
      Given relevant talk-page revisions are locally available
      When talk context is summarized
      Then the receipt identifies the searched range and matching public revisions
      And absence is reported only for the searched evidence

    Scenario: Protection and page operations retain source links
      Given public logs record relevant page operations
      When process context is assembled
      Then operation type, timestamp, actor state, and source link are retained where available

    Scenario: Process signals cannot confirm an event
      Given reverts, talk activity, protection, or account overlap is observed
      When the final L1 state is resolved
      Then only exact content evidence controls confirmation
      And process context cannot promote a rejected or unavailable candidate

  @implemented @policy
  Rule: Interpretation stays action-level

    Scenario: An attribution receipt includes a scope disclaimer
      When a revision attribution receipt is printed or published
      Then it states that it describes public revision actions
      And it does not establish bias, intent, coordination, ownership, misconduct, or real-world identity

    Scenario: Concentration labels remain disabled without calibration
      Given attribution shares are available
      But benchmark distributions do not support discriminating concentration thresholds
      When summaries are generated
      Then raw shares and counts may be displayed
      And no high-concentration or coordinated-action label is assigned

  @gap
  Rule: Attribution calibration must precede qualitative labels

    Scenario: A future concentration label is benchmark-gated
      Given a proposed qualitative concentration label
      When it is considered for implementation
      Then neutral and positive-control distributions are documented
      And precision, false-positive behavior, and threshold stability are measured
      And the label remains disabled until the calibration gate passes
