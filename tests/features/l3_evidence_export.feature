@tool @l3
Feature: L3 inspectable redline and authorship export
  Researchers need static evidence pages that expose exact or evaluated candidate text changes
  and token provenance without changing the analytical verdict.

  Background:
    Given a canonical article corpus and a current L1 confirmation receipt exist

  @implemented @offline
  Rule: Export state follows exact confirmation authority

    Scenario: Confirmed events export exact redlines
      Given one or more exact events are confirmed
      When L3 diff export runs
      Then one redline is exported for each supported exact event
      And each page identifies exact before and after revisions
      And candidate-level boundaries cannot replace exact boundaries

    Scenario: Rejected candidates remain exportable for audit
      Given exact checking rejected an evaluated coarse candidate
      When candidate export runs
      Then a candidate-relative redline may be exported
      And the page is labeled candidate and rejected
      And it is not included in confirmed-event counts

    Scenario: Healthy articles do not fabricate event diffs
      Given current L1 state is healthy
      When L3 export runs
      Then no confirmed or candidate event redline is fabricated
      And the export reports that no eligible event boundary exists

    Scenario: Legacy coarse pivots are withheld
      Given only a legacy coarse pivot exists
      And current exact candidate receipts are absent or incompatible
      When L3 resolves exportable pivots
      Then the legacy pivot is withheld
      And unavailable is reported rather than exporting misleading evidence

  @implemented @offline
  Rule: Redlines preserve readable source text

    Scenario: Added, removed, and unchanged text are distinguishable
      Given an eligible revision pair
      When its redline is rendered
      Then additions, removals, and unchanged context are visually and textually distinguishable
      And additions and removals do not rely on color alone

    Scenario: Source revisions remain directly inspectable
      Given a redline page is exported
      When a researcher inspects its provenance
      Then it links to both public Wikipedia oldids
      And it identifies the comparison mode, event or candidate identity, and analytical outcome

    Scenario: Empty or unavailable content fails visibly
      Given either revision lacks usable source text
      When redline export runs
      Then no empty successful diff is written
      And the export records unavailable with a reason

  @implemented @offline
  Rule: Authorship coloring is provenance evidence

    Scenario: Supported spans retain origin actors
      Given source history supports token-origin attribution
      When blame export runs
      Then contiguous spans with the same supported origin are grouped for display
      And each span retains origin revision and public actor state

    Scenario: Unknown authorship remains unknown
      Given a token's origin cannot be supported from available history
      When blame is rendered
      Then the span is marked unknown or unavailable
      And it is not assigned to a nearby actor by inference

    Scenario: Authorship has a textual legend
      Given multiple actor colors are displayed
      When the authorship visualization is inspected
      Then a text legend maps each color to its literal public actor token or state
      And the evidence remains interpretable without color

  @implemented
  Rule: Static export is deterministic and bounded

    Scenario: Repeated export yields stable filenames and ordering
      Given inputs and contracts are unchanged
      When L3 export runs twice
      Then equivalent event and candidate pages use the same safe paths
      And displayed rows and legends have deterministic order

    Scenario: Article and event identifiers cannot escape output roots
      Given an input title or identifier contains unsafe path characters
      When an output filename is constructed
      Then the resulting path remains beneath the configured article output directory

  @policy
  Rule: L3 adds inspectability rather than authority

    Scenario: A compelling visual does not alter state
      Given a redline appears large or an actor share appears concentrated
      When L3 output is consumed
      Then the upstream exact decision remains unchanged
      And the visualization does not infer bias, intent, ownership, coordination, or misconduct

  @gap
  Rule: L3 should become a discoverable first-class tool surface

    Scenario: Users can invoke and inspect L3 from the supported command interface
      Given an eligible article finding exists
      When a user requests L3 export through the documented tool interface
      Then exact and candidate export modes are discoverable
      And machine-readable status and output paths are returned
      And the implementation does not require importing a viewer script directly
