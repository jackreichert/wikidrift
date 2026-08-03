@layers @l5 @living-documentation
Feature: Compare an article with evidence outside its English timeline
  As a researcher
  I want language, fact, and citation comparisons
  So that I can investigate differences that historical change detection alone cannot reveal

  Rule: L5 cross-language stance comparison preserves entity-relative evidence

    Scenario: Compare stance across language editions
      Given comparable passages exist in multiple language editions
      When L5 classifies how each passage treats the named entity
      Then each label retains its supporting passage and edition
      And disagreement is reported without declaring which edition is correct

    Scenario: Compare change around an English rewrite
      Given a fresh L1 confirmation supplies an exact temporal anchor
      When L5 compares language-edition stance around that event
      Then the comparison identifies the exact English revisions used
      And it distinguishes a temporal change from a static difference between editions

  Rule: L5 cross-language lead comparison shows concrete claims and omissions

    Scenario: Prefer a fresh exact comparison window
      Given a fresh L1 confirmation is available
      When L5 compares article openings across languages
      Then English is represented by the exact stable before and after versions
      And the comparison is labeled pivot-relative

    Scenario: Fall back without pretending exact confirmation
      Given no fresh exact L1 confirmation is available
      When L5 compares article openings across languages
      Then it uses a coarse candidate window or current leads when available
      And it labels the comparison candidate-relative or static

    Scenario Outline: Preserve a cross-language comparison verdict
      Given a concrete question was compared across editions
      And the supported outcome is "<outcome>"
      When L5 publishes the comparison
      Then it retains the excerpts supporting "<outcome>"
      And it does not strengthen the outcome beyond the evidence

      Examples:
        | outcome               |
        | agree                 |
        | compatible difference |
        | contradiction         |
        | insufficient          |

  Rule: L5 fact and claim checks are edition-aware and time-aware

    Scenario: Compare a factual claim as of a historical date
      Given suitable revisions exist for the requested date in multiple editions
      When L5 checks a factual question
      Then each answer is tied to its edition and historical revision
      And agreement, compatible difference, contradiction, and insufficient evidence remain distinct

    Scenario: Preserve missing factual evidence
      Given an edition does not contain enough information to answer a factual question
      When L5 reports the comparison
      Then that edition is marked insufficient
      And silence is not interpreted as disagreement

  Rule: L5 citation history reports composition without rating sources

    Scenario: Compare citations across a rewrite
      Given suitable article versions exist before and after a rewrite window
      When L5 compares the article's citations
      Then it reports which cited domains or works were added, removed, increased, or decreased
      And each comparison identifies the versions used
      And no source receives a reliability, ideology, or quality rating

    Scenario: Compare citation history without an exact pivot
      Given no exact rewrite window is available
      But suitable historical versions exist
      When L5 compares citations
      Then it labels the fallback comparison accurately
      And it does not imply that citation change proves a factual or framing change
