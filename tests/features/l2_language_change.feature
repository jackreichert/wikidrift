@layers @l2 @living-documentation
Feature: Explain changes in language and framing over time
  As a researcher
  I want semantic and lexical comparisons tied to inspectable text
  So that I can distinguish wording turnover from a possible change in meaning

  Rule: L2 compares entity-relative stance over time

    Scenario: Publish a supported stance comparison
      Given stored passages are available for multiple points in an article's history
      When L2 compares how the passages treat the named entity
      Then each published classification appears with its exact supporting passage
      And the compared dates or revisions are identified
      And the output is presented as model-assisted evidence rather than a neutrality verdict

    Scenario: Repeat an apparent stance transition
      Given adjacent observations receive different stance labels
      When L2 checks the apparent transition
      Then both observations are classified repeatedly
      And unstable model labels are identified separately from changes in the compared text

    Scenario: Decline to overstate weak evidence
      Given the available passages or repeated classifications do not support a stable comparison
      When L2 reports the result
      Then it marks the evidence insufficient or unstable
      And it does not invent a stance shift

  Rule: L2a traces sentence-level claims through stable revisions

    Scenario: Classify the trajectory of a claim
      Given exact stable revisions are available
      When L2a compares sentence-level claims across those revisions
      Then each published claim is labeled added, removed, retained, or relocated
      And additions that remain standing are distinguishable from temporary additions

    Scenario: Use additive trajectory as a research lead
      Given a claim was added and remained standing
      When L2a publishes the trajectory
      Then the claim is linked to the compared revision sequence
      And its persistence is not presented as proof that the claim is correct or biased

  Rule: L2.5 shows vocabulary change without assigning motive

    Scenario: Compare vocabulary around a rewrite
      Given L1 provides a usable confirmed or candidate window
      When L2.5 compares the surrounding snapshots
      Then it lists terms overrepresented before and after the window
      And it identifies the versions used in the comparison
      And it reports the measured lexical divergence

    Scenario: Compare vocabulary without a rewrite window
      Given no usable L1 window is available
      But oldest and newest suitable snapshots are available
      When L2.5 runs
      Then it labels the comparison as a whole-history comparison
      And it does not imply that an exact rewrite event was found

    Scenario: Flag an unsuitable lexical baseline
      Given the before and after texts differ too greatly in size for a balanced comparison
      When L2.5 reports vocabulary change
      Then it marks the baseline as insufficient
      And it keeps the raw comparison inspectable without promoting it to a strong finding
