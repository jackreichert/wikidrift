@website @living-documentation
Feature: Explore WikiDrift evidence on the website
  As a reader investigating how a Wikipedia article changed
  I want a clear path from summaries to inspectable evidence
  So that I can form research questions without mistaking a lead for a verdict

  Background:
    Given WikiDrift has published a collection of analyzed articles

  Scenario: Find an article from the collection
    When I browse the findings collection
    Then I see a search control and the available topic filters
    And each result shows the article title, summary, topic, and available analysis markers

  Scenario: Open an article report
    Given an article is included in the published collection
    When I open its report
    Then I see a plain-English overview before the detailed evidence
    And I see navigation for each available evidence section
    And selecting a section displays its evidence
    And the report does not present a single bias score

  Scenario: Follow a summary back to evidence
    Given an article report summarizes a finding
    When I inspect the related evidence section
    Then I see the wording, revisions, citations, or structured finding behind the summary
    And public revision evidence includes a link to its source when one is available

  Scenario: Understand missing coverage
    Given an analysis layer is unavailable for an article
    When I open the article report
    Then the missing layer is labeled unavailable or not run
    And the notice gives a reason when one is known
    And it does not describe missing coverage as a negative finding

  Scenario: Use the report without relying on color
    When I navigate an article report without a pointing device
    Then every primary control is reachable in a logical order
    And the currently focused control is visibly indicated
    And statuses are communicated with text as well as color
    And tables expose meaningful row and column headings

  Rule: The Overview section summarizes evidence cautiously

    Scenario: Read the overview
      Given an article has one or more published findings
      When I open its Overview section
      Then I see which evidence is available
      And I see which evidence is missing
      And change, disagreement, and editor activity are described as context rather than proof of intent

  Rule: The Rewrite section explains durable change

    Scenario: Inspect every available rewrite interval
      Given an article has been analyzed with one or more stored snapshots
      When I open the Rewrite section
      Then I see a "Persistence-weighted change by interval" chart
      And I see persistence-weighted loss, standing gain, and replacement leads for every measured interval
      And replacement leads are not presented as confirmed semantic replacement
      And any unavailable or excluded interval remains visible with its missing-data reason
      And descriptive, candidate, confirmed, rejected, excluded, and measured states are distinguishable

    Scenario: Keep a sub-floor anomaly visible
      Given a measured interval crosses a sweep anomaly floor
      But it starts below the exact-check token floor
      When I open the Rewrite section
      Then the interval is labeled a descriptive anomaly
      And the report does not describe it as excluded or healthy

    Scenario: Keep the interval chart visible when measurements are missing
      Given an analyzed article has incomplete snapshot or interval evidence
      When I open the Rewrite section
      Then I still see a "Persistence-weighted change by interval" chart
      And the chart identifies which measurements are missing and why when known
      And missing measurements are not presented as zero loss or a negative finding

    Scenario Outline: Inspect every completed rewrite investigation
      Given a candidate rewrite investigation completed with outcome "<outcome>"
      And its public before and after revisions pass the publication trust checks
      When I open the Rewrite section
      Then I can open a redline for that investigated revision pair
      And the redline labels the investigation outcome as "<outcome>"

      Examples:
        | outcome   |
        | confirmed |
        | rejected  |

  Rule: The Vocabulary section explains changed wording

    Scenario: Inspect vocabulary differences
      Given a vocabulary comparison is available
      When I open the Vocabulary section
      Then I see words that became more or less common
      And the comparison identifies the versions or period being compared
      And vocabulary change is not presented as a score of bias

  Rule: The Citations section explains source-mix change

    Scenario: Inspect citation changes
      Given citation-history evidence is available
      When I open the Citations section
      Then I see which cited domains or works increased or decreased
      And the website does not rate a source as reliable, unreliable, biased, or neutral

  Rule: The Framing section compares article openings

    Scenario: Inspect framing across language editions
      Given a cross-language lead comparison is available
      When I open the Framing section
      Then I see which language editions and versions were compared
      And I can inspect the excerpts supporting each comparison
      And disagreement is presented as a reason to investigate rather than a final answer

  Rule: The Facts section preserves comparison states

    Scenario Outline: Read a factual comparison outcome
      Given a factual question was checked across language editions
      And its outcome is "<outcome>"
      When I open the Facts section
      Then the website displays "<outcome>" without converting it into a stronger claim

      Examples:
        | outcome               |
        | agree                 |
        | compatible difference |
        | contradiction         |
        | insufficient          |

  Rule: The Versions section makes the comparison reproducible

    Scenario: Inspect compared revisions
      Given the report compares stored article versions
      When I open the Versions section
      Then I see the language, date, and revision identity for each version
      And I can follow available links to the public Wikipedia revision
