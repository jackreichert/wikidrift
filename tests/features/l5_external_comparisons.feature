@tool @l5
Feature: L5 external-reference and citation-composition analysis
  Researchers need to compare independent language editions and citation composition
  as contextual evidence without treating any edition or source class as ground truth.

  @implemented @network
  Rule: Cross-language snapshots are independently sourced

    Scenario: Every confirmed event receives episode-relative L5 evidence
      Given fresh L1 state contains multiple confirmed events
      When citation, framing, and fact analyses run for the article
      Then each citation and framing result identifies its event's exact before and after revisions
      And each fact result identifies its event's exact post-event timestamp
      And every result is retained under its exact revision-pair identity
      And one unavailable event does not suppress a completed sibling event

    Scenario: Available editions resolve localized titles and revisions
      Given target languages are requested
      When edition discovery runs
      Then each available edition records language, localized title, revision ID, timestamp, and source URL
      And missing editions remain explicitly unavailable

    Scenario: Temporal selection respects the requested mode
      Given an exact event timestamp or candidate-relative timestamp exists
      When language snapshots are selected
      Then each edition uses the documented at-or-before temporal rule where supported
      And static latest-snapshot mode is labeled separately

    Scenario: Retrieval failure is not agreement
      Given one or more required editions cannot be retrieved or parsed
      When comparison is resolved
      Then the affected result is unavailable or insufficient evidence
      And no no-divergence or agreement result is emitted from missing text

    Scenario: Language auto-selection is article-derived
      Given target languages are not supplied
      When L5 chooses editions
      Then selection is derived from available article language links and documented limits
      And chosen and excluded languages are recorded

  @implemented @llm
  Rule: Cross-language stance differences remain auditable

    Scenario: A framing divergence retains quotations
      Given comparable edition text is available
      And the configured model returns a valid structured result
      When stance comparison is persisted
      Then each divergence identifies the compared languages, focal entity, verdict, explanation, and supporting quotations where present
      And the prompt, provider, model, and source revisions are recorded

    Scenario: No-divergence requires usable comparisons
      Given all required edition texts meet adequacy requirements
      And valid adjudication finds no supported divergence
      When the result is emitted
      Then no divergence detected is permitted
      And the statement remains bounded to the selected editions, snapshots, model, and prompt

    Scenario: Model instability fails closed
      Given repeated or validated outputs are inconsistent beyond the stability contract
      When framing results are resolved
      Then the result is unstable or unavailable
      And disagreement is preserved for audit

  @implemented @offline
  Rule: Cross-language lead comparison is deterministic

    Scenario: Leads are extracted from comparable revision text
      Given two or more usable language snapshots exist locally
      When lead comparison runs
      Then each lead boundary and normalized text are retained
      And deterministic similarity or divergence metrics are computed

    Scenario: Short or empty leads are insufficient
      Given an edition lead fails minimum adequacy
      When lead comparison runs
      Then the pair is insufficient evidence
      And it is not scored as maximally similar or divergent

    Scenario: Lead difference is not truth difference
      Given leads differ substantially
      When the result is reported
      Then it describes emphasis or vocabulary difference at the selected snapshots
      And it does not declare either edition more accurate, neutral, or authoritative

  @implemented @llm
  Rule: Fact divergence preserves a bounded verdict schema

    Scenario Outline: Fact comparison retains its epistemic state
      Given a supported comparison question is evaluated across usable editions
      And the valid verdict is <verdict>
      When the result is persisted
      Then the verdict remains <verdict>
      And the question, compared revisions, explanation, and evidence quotations are retained

      Examples:
        | verdict              |
        | contradiction        |
        | compatible difference|
        | agreement            |
        | insufficient evidence|

    Scenario: Unsupported claims cannot become contradictions
      Given the model output lacks adequate evidence in one or more editions
      When fact comparison is validated
      Then the verdict is insufficient evidence
      And contradiction is rejected

    Scenario: An article without configured questions remains citation-only
      Given no factual comparison questions are configured for an article
      When L5 fact analysis runs
      Then citation context is retained for the selected editions
      And no model-assisted claim extraction or adjudication runs
      And the empty claim comparison is not presented as agreement

  @implemented @offline
  Rule: Citation-source composition is descriptive

    Scenario: Citation inventory preserves domains and types
      Given comparable article snapshots contain citations
      When citation extraction runs
      Then before and after domain counts, citation-type counts, additions, and removals are recorded
      And source URLs remain inspectable where available

    Scenario: Missing citations differ from failed extraction
      Given one article genuinely has no supported citations and another cannot be parsed
      When states are resolved
      Then the first may report an observed empty inventory
      And the second reports unavailable

    Scenario: No reliability score is assigned
      Given citation domains or types change
      When composition is reported
      Then the tool describes those changes
      And it does not classify a source as reliable, unreliable, good, bad, mainstream, or fringe

  @implemented @offline
  Rule: Controversy context is corroborative

    Scenario: M-score reports observable volatility inputs
      Given sufficient local revision history exists
      When M-score is computed
      Then supported revision, revert, contributor, and age features are recorded under the documented formula
      And the score is identified as context or pre-ranking

    Scenario: M-score cannot confirm drift
      Given an article has a high M-score
      When article status is resolved
      Then L1 exact evidence remains authoritative
      And M-score alone cannot produce a rewrite finding

  @policy
  Rule: Wikipedia editions are comparators rather than ground truth

    Scenario: L5 carries a comparison disclaimer
      Given any L5 output is printed or published
      When a researcher reads the comparison
      Then it states that independent editions and citation composition provide comparison context
      And it does not determine which account, edition, claim, or source is right

  @gap
  Rule: External non-Wikipedia reference corpora require a new evidence contract

    Scenario: A future external corpus preserves provenance and temporal comparability
      Given a non-Wikipedia reference source is proposed
      When it is admitted to L5
      Then licensing, retrieval time, source identity, revisionability, quote provenance, and temporal alignment are specified
      And it is not silently treated as ground truth
