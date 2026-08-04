@tool @l0
Feature: L0 canonical corpus and integrity
  Downstream analysis needs a complete, canonical, locally reproducible history
  whose endpoint and provenance are known before any analytical claim is made.

  Background:
    Given a requested Wikipedia article has resolved to a canonical page identity

  @implemented @network
  Rule: Collection uses public revision evidence

    Scenario: A new article history is collected into its owner shard
      Given no complete local corpus exists
      When collection runs
      Then revisions are fetched from public Wikipedia endpoints
      And revision metadata, content, parent relationships, and continuation state are persisted locally
      And the resulting corpus is owned by the canonical article shard

    Scenario: Collection resumes after interruption
      Given a previous collection saved a continuation checkpoint
      When collection resumes
      Then it continues from the saved checkpoint
      And it does not duplicate already persisted revisions
      And completion is not claimed until pagination is exhausted

    Scenario: A complete unchanged corpus is reused
      Given the local corpus is complete through its recorded horizon
      When a local-only downstream analysis runs
      Then the corpus is read without a network request
      And the evidence horizon remains explicit

  @implemented
  Rule: Canonical identity prevents split evidence

    Scenario: Redirect and canonical title share one corpus
      Given two accepted inputs resolve to the same page identity
      When their data location is resolved
      Then both inputs select the same article-owned storage
      And findings use the canonical title and page ID

    Scenario: A page identity mismatch is quarantined
      Given persisted rows contain an unexpected page identity for the canonical article
      When integrity is checked
      Then the affected evidence is quarantined or rejected
      And downstream analysis receives unavailable rather than mixed-page evidence

  @implemented
  Rule: Integrity distinguishes complete from usable

    Scenario Outline: A corpus cannot be complete with a structural defect
      Given the corpus contains <defect>
      When integrity is evaluated
      Then the corpus is partial or quarantined
      And exact downstream evidence is withheld

      Examples:
        | defect                              |
        | unresolved continuation pagination |
        | duplicate revision identity        |
        | missing required content            |
        | broken parent linkage               |
        | inconsistent canonical page identity |

    Scenario: Integrity repair is idempotent
      Given a deterministic repair is available for a known local defect
      When repair runs more than once
      Then the resulting canonical rows are unchanged after the first successful repair
      And no valid evidence is discarded

    Scenario: Quarantined rows remain auditable
      Given integrity checking excludes one or more rows
      When an operator inspects the corpus report
      Then the excluded rows and reasons are identifiable
      And analytical code cannot silently consume them

  @implemented
  Rule: Stable endpoints anchor reproducibility

    Scenario: A complete corpus records stable boundary evidence
      Given collection completes
      When corpus metadata is persisted
      Then the earliest and latest usable revision identities and timestamps are recorded
      And downstream receipts can state the corpus horizon

    Scenario: Endpoint change invalidates dependent freshness
      Given a dependent finding was computed through revision A
      And the corpus now has a later authoritative endpoint B
      When freshness is checked
      Then the dependent finding is stale unless its contract explicitly permits the older frozen horizon

  @implemented
  Rule: Article shards are safely consolidated

    Scenario: Shard merge preserves canonical rows
      Given independent article-owned shards contain non-conflicting canonical data
      When consolidation runs
      Then each canonical revision appears once in the merged store
      And finding ownership remains attributable to its article

    Scenario: Merge conflict fails visibly
      Given two shards claim different canonical content for the same revision identity
      When consolidation runs
      Then the conflict is reported
      And neither variant silently overwrites the other

  @implemented
  Rule: Corpus metadata supports audit and capacity planning

    Scenario: Corpus inspection reports evidence coverage
      Given a local article corpus exists
      When an operator inspects it
      Then revision count, usable content count, first and last timestamps, endpoint status, and integrity state are available

    Scenario: Discovery views are derived from canonical data
      Given editor, page, link, or revision summary views are built
      When they are queried
      Then each row traces to canonical public revisions
      And derived views do not become independent evidence authorities
