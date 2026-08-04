@tool
Feature: Cross-cutting WikiDrift tool contracts
  Researchers need every layer to use canonical public evidence, preserve uncertainty,
  and emit reproducible receipts without converting signals into accusations.

  @policy
  Rule: Every output is a bounded research lead

    Scenario: Observable evidence never becomes an intent verdict
      Given any detector, corroborator, graph, or model-assisted layer produces output
      When the output is persisted, printed, exported, or published
      Then it describes observable content, revision, citation, or edition evidence
      And it does not claim bias, neutral truth, motive, identity, coordination, ownership, policy violation, or misconduct

    Scenario: Necessary and sufficient evidence remain separate
      Given long-stable text was removed
      When the evidence is interpreted
      Then the tool may report durable content loss
      But it cannot report that meaning reversed without semantic evidence
      And it cannot report that the change was improper without human adjudication and external context

    Scenario: Topic defaults remain neutral
      Given no explicit focal entities, languages, or overlays are supplied
      When an analysis layer chooses defaults
      Then it derives them from the article and available editions
      And it does not inject a controversy-specific preferred framing

  @implemented
  Rule: Article inputs resolve consistently

    Scenario Outline: Single-article commands accept supported inputs
      Given the user supplies <input>
      When a single-article command normalizes it
      Then the resulting article argument is <title>

      Examples:
        | input                                                        | title             |
        | Chess                                                        | Chess             |
        | https://en.wikipedia.org/wiki/Elizabeth_Warren               | Elizabeth Warren  |
        | https://en.wikipedia.org/w/index.php?title=Elizabeth_Warren  | Elizabeth Warren  |

    Scenario: Redirects resolve before storage ownership
      Given a requested title redirects to a canonical Wikipedia article
      When the tool opens or creates article data
      Then canonical title and page identity are resolved first
      And aliases do not create independent analytical identities

    Scenario: Missing articles fail before evidence is written
      Given the requested article does not exist
      When canonical resolution runs
      Then the command fails with an article-not-found error
      And no partial analytical finding is persisted

  @implemented
  Rule: Findings are versioned and atomic

    Scenario: A finding records its analytical contract
      Given a layer persists a finding
      When the finding is inspected
      Then the finding records article identity, run time, schema or prompt contract, and evidence horizon appropriate to the layer
      And its raw evidence is sufficient to recompute displayed summaries where practical

    Scenario: Finding writes are atomic
      Given a finding is being replaced
      When serialization succeeds
      Then readers observe either the previous complete file or the new complete file
      And they never observe a partially written JSON document

    Scenario: Missing findings load as explicit absence
      Given no finding exists for an article and layer
      When a consumer loads it with a documented default
      Then the consumer receives that default
      And absence is not interpreted as a negative analytical result

  @implemented @policy
  Rule: Freshness controls downstream authority

    Scenario Outline: A confirmation is stale when a contract dimension differs
      Given a persisted confirmation exists
      And <dimension> differs from the current analytical contract
      When freshness is evaluated
      Then the confirmation is stale
      And it cannot remain authoritative downstream

      Examples:
        | dimension          |
        | schema version     |
        | threshold contract |
        | corpus horizon     |

    Scenario: A persisted legacy receipt without a schema is stale
      Given a saved confirmation has a run timestamp but no schema version
      When freshness is evaluated
      Then the confirmation is stale
      And a refresh is required to publish current interval and candidate receipts

    Scenario: A fresh exact result overrides a coarse result
      Given coarse analysis found a candidate
      And current exact analysis rejected it
      When pipeline, lexical, L3, L4, export, or website logic resolves state
      Then not_confirmed is authoritative
      And the coarse candidate cannot be counted as corroboration or confirmation

  @implemented
  Rule: Configuration is external and deterministic

    Scenario: Article-owned storage is selected before module initialization
      Given WIKIDRIFT_DATA_DIR points to an article shard
      When a WikiDrift process starts
      Then databases, findings, caches, and logs resolve beneath that directory
      And the process does not share a writable canonical database with another shard worker

    Scenario: Explicit CLI LLM settings override environment defaults
      Given provider, model, or base URL is supplied on the command line
      When an LLM client is configured
      Then explicit arguments take precedence over environment configuration

    Scenario: Unknown or malformed configuration fails clearly
      Given a provider, model, pricing document, mode, or threshold contract is invalid
      When configuration is parsed
      Then the command fails before analytical output is published
      And the error identifies the invalid contract

  @implemented
  Rule: Failure does not masquerade as evidence

    Scenario Outline: Inadequate upstream evidence fails closed
      Given upstream evidence is <condition>
      When a dependent layer runs
      Then its analytical state is unavailable or not_applicable as appropriate
      And it does not emit healthy, agreement, or no-difference as a substitute

      Examples:
        | condition            |
        | partial              |
        | quarantined          |
        | unstable             |
        | stale                |
        | schema-incompatible  |
        | retrieval failed     |

    Scenario: Network and LLM calls use bounded retry behavior
      Given a retryable timeout, rate limit, or server error occurs
      When a network-backed operation runs
      Then retry count and delay are bounded
      And a non-retryable client error surfaces immediately
      And final exhaustion produces unavailable or command failure rather than a fabricated result

    Scenario: Offline commands never contact external services
      Given a command is documented and tagged offline
      When it runs against an unchanged local corpus
      Then no HTTP or LLM call occurs
      And repeated calculations produce the same analytical values

  @implemented @policy
  Rule: Public account handling preserves evidentiary limits

    Scenario: Public account names remain literal
      Given structured public history attributes an action to a registered account
      When the action is included in a receipt
      Then the public account token may be retained literally
      And separate account names are never merged into an inferred identity

    Scenario: Account states remain distinct
      Given event revisions include registered, bot, anonymous IP, hidden, renamed, or unavailable account states
      When attribution is computed
      Then those states remain distinguishable
      And graph eligibility excludes bot, anonymous IP, hidden, and unavailable identities
      And no real-world identity is inferred

  @implemented
  Rule: CLI outcomes are automation-friendly

    Scenario: Successful commands exit successfully
      Given all required stages complete
      When a CLI verb returns
      Then its process exit status is zero
      And requested JSON mode writes parseable JSON to standard output

    Scenario: Partial backfill failure exits unsuccessfully
      Given one or more requested episode backfills fail
      When the backfill command completes its report
      Then it reports updated, unchanged, and failed counts
      And its process exit status is non-zero

    Scenario: Human reports distinguish commands from analyses
      Given a batch contains completed findings, completed negative results, unavailable analyses, and failed commands
      When the summary is printed
      Then those four outcomes are counted separately

  @gap
  Rule: Documentation and tests agree on network boundaries

    Scenario: Every command has one verified network classification
      Given the CLI command catalog
      When documentation and automated tests are reviewed
      Then each command is classified as offline, cache-dependent, network-backed, or LLM-backed
      And no command that may fetch public APIs is described as unconditionally offline
