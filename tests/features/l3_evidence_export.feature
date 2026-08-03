@layers @l3 @living-documentation
Feature: Export inspectable rewrite and authorship evidence
  As a website reader
  I want analysis summaries connected to readable article evidence
  So that I can inspect what changed and where the current wording came from

  Rule: L3 exports a readable before-and-after redline

    Scenario: Export a confirmed rewrite
      Given an article has a fresh confirmed L1 event
      And the exact stable before and after revisions are available
      When L3 prepares the rewrite evidence
      Then the export identifies both public revisions and their dates
      And removed wording is marked as deleted text
      And replacement wording is marked as inserted text
      And the redline remains evidence of change rather than proof of motive or correctness

    Scenario: Export a rejected rewrite investigation
      Given L1 investigated a candidate and did not confirm the required durable-spine drop
      And the investigated public before and after revisions are available
      When L3 prepares the rewrite evidence
      Then L3 still exports a readable redline for the investigated revision pair
      And the export labels the exact outcome as rejected
      And it does not describe the candidate as a confirmed rewrite

    Scenario: Label a fallback comparison honestly
      Given no fresh confirmed L1 event is available
      But suitable article versions can be compared
      When L3 prepares rewrite evidence
      Then the export identifies how the fallback versions were chosen
      And it does not label the comparison as an exact confirmed event

    Scenario: Withhold untrusted rewrite evidence
      Given a rewrite export references stale, quarantined, or unverifiable revisions
      When L3 prepares evidence for publication
      Then the rewrite artifact is withheld with a reason
      And the website does not publish it as current evidence

  Rule: L3 exports public authorship for the current lead

    Scenario: Trace current wording to public revision origins
      Given token provenance is available for the current article lead
      When L3 prepares the authorship overlay
      Then adjacent wording with the same origin may be grouped into readable spans
      And each published span shows its public account, origin revision, and origin date when available
      And unknown provenance remains visibly unknown

    Scenario: Interpret authorship as action rather than intent
      Given a public account introduced wording that remains in the current lead
      When the authorship overlay is published
      Then the overlay describes the observable origin of that wording
      And it does not infer the account's identity, motive, coordination, or factual correctness
