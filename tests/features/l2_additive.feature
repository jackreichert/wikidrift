@tool @l2a
Feature: L2a deterministic formative and additive trajectory
  Researchers need to see how claims accumulate, disappear, persist, or move across snapshots
  using deterministic text evidence that complements rather than replaces stance analysis.

  Background:
    Given two or more comparable article snapshots exist

  @implemented @offline
  Rule: Snapshot text is segmented deterministically

    Scenario: Comparable units retain source provenance
      When snapshot text is segmented into supported units
      Then every unit retains its source revision, position, and text
      And repeated processing yields the same segmentation

    Scenario: Boilerplate and unusable text are excluded consistently
      Given snapshots contain empty, markup-only, or unsupported units
      When segmentation runs
      Then exclusions follow one documented deterministic rule
      And exclusion counts remain auditable

  @implemented @offline
  Rule: Additive states describe text relationships

    Scenario: Newly introduced claims are added
      Given a supported unit has no matched predecessor
      When adjacent snapshots are compared
      Then the unit is classified as added
      And its after-snapshot provenance is retained

    Scenario: Disappearing claims are removed
      Given a supported before unit has no matched successor
      When adjacent snapshots are compared
      Then the unit is classified as removed
      And its before-snapshot provenance is retained

    Scenario: Stable claims are retained
      Given a before unit has an adequate supported successor match
      When adjacent snapshots are compared
      Then the unit is classified as retained
      And match evidence and score are retained

    Scenario: Relocated claims are distinguished from removal
      Given semantically comparable text survives at a materially different location
      When positional comparison runs
      Then the relationship may be classified as relocated
      And it is not double-counted as both removed and added

    Scenario: Ambiguous matching fails closed
      Given multiple successor units are equally plausible under the match contract
      When classification runs
      Then no unsupported one-to-one match is asserted
      And ambiguity remains visible in the result

  @implemented
  Rule: Trajectories preserve each transition

    Scenario: Multi-snapshot analysis forms ordered transitions
      Given snapshots A, B, and C are comparable
      When L2a trajectory is computed
      Then A-to-B and B-to-C results remain separately inspectable
      And aggregate counts can be recomputed from transition receipts

    Scenario: Event-relative mode uses exact boundaries
      Given a fresh exact L1 event exists
      When L2a selects its primary transition
      Then it compares exact before and after event revisions
      And it labels any broader context snapshots separately

    Scenario: Missing comparison adequacy prevents a claim
      Given a snapshot is empty, inaccessible, or below the adequacy contract
      When a transition is requested
      Then the transition is unavailable
      And zero additions or removals are not fabricated

  @implemented @policy
  Rule: Deterministic text states do not infer meaning or merit

    Scenario: Additive output uses descriptive wording
      Given a formative trajectory exists
      When it is reported
      Then added, removed, retained, and relocated refer to text relationships
      And the result does not claim factual correction, censorship, bias, neutrality, or intent

    Scenario: L2a and L2 remain independent
      Given L2 stance and L2a additive results both exist
      When the pipeline summarizes them
      Then neither result overwrites the other
      And disagreement is preserved for human review
