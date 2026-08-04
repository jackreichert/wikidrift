@tool @pipeline
Feature: Pipeline, batch, and operational orchestration
  Operators need deterministic routing from L1 state to dependent analyses,
  isolated article processing, resumable execution, and honest aggregate reports.

  @implemented
  Rule: The pipeline resolves upstream authority before routing

    Scenario: Fresh confirmation is reused
      Given a current L1 confirmation matches corpus horizon, schema, and thresholds
      When the article pipeline starts
      Then exact confirmation is not recomputed unnecessarily
      And the existing receipt controls downstream state

    @gap
    Scenario: Stale confirmation is refreshed first
      Given L1 confirmation is stale or legacy
      When the article pipeline starts
      Then L1 is recomputed before dependent event-relative stages
      And no dependent stage consumes the stale event boundary

    Scenario Outline: Downstream routing follows authoritative L1 state
      Given current L1 state is <state>
      When the pipeline plans dependent stages
      Then event-relative stages receive <routing>

      Examples:
        | state         | routing                                      |
        | confirmed     | exact event boundaries                       |
        | not_confirmed | evaluated rejection evidence where supported|
        | healthy       | non-event mode or not_applicable              |
        | unavailable   | unavailable without fabricated fallback       |

    Scenario: Requested levels are dependency-closed
      Given the user requests a higher level
      When the execution plan is built
      Then required lower-layer evidence is included or verified
      And unrelated optional levels are not run

  @implemented
  Rule: Article processing is isolated

    Scenario: Every confirmed episode receives complete downstream analysis
      Given an article has multiple fresh confirmed L1 episodes
      When the full article pipeline runs
      Then every episode has an independent vocabulary, citation composition, framing, and fact result
      And each vocabulary, citation composition, and framing result identifies that episode's exact before and after revisions
      And each fact result identifies that episode's exact post-event timestamp

    Scenario: Episode findings accumulate without overwrite
      Given two confirmed episodes complete the same downstream stage
      When the article finding is persisted
      Then both episode results remain in one article artifact keyed by exact revision pair
      And the primary compatibility fields do not replace or hide the other episode

    Scenario: One episode failure does not suppress its siblings
      Given one confirmed episode is unavailable for a downstream stage
      And another confirmed episode can complete that stage
      When the article pipeline finishes
      Then the unavailable episode retains its reason
      And the completed sibling remains available
      And other downstream stages continue independently for both episodes

    Scenario: Each worker owns one article shard
      Given a batch analyzes multiple articles concurrently
      When workers start
      Then each worker receives a distinct WIKIDRIFT_DATA_DIR
      And workers do not concurrently write one shared canonical DuckDB file

    Scenario: One article failure does not corrupt another
      Given article A fails during collection or analysis
      And article B can complete
      When the batch finishes
      Then article B's complete findings remain valid
      And article A is reported failed or unavailable with its reason

    Scenario: Merge occurs after worker completion
      Given article workers have completed isolated writes
      When consolidation begins
      Then only complete eligible shard artifacts are merged
      And conflicts fail visibly rather than last-writer-wins

  @implemented
  Rule: Batch execution resumes safely

    Scenario: Completed compatible work is skipped
      Given an article-stage receipt is complete and current
      And its recorded command contract matches the requested command
      When a resumable batch reruns
      Then that stage is skipped
      And its prior result remains counted once

    Scenario: A changed stage command invalidates its resume checkpoint
      Given a stage name is recorded as complete
      But the requested command adds or changes analysis options
      When a resumable batch reruns
      Then that stage runs with the requested command contract
      And the new command contract replaces the prior resume checkpoint

    Scenario: A legacy stage-only checkpoint is refreshed once
      Given a stage name is recorded as complete without its command contract
      When a resumable batch reruns
      Then that stage runs rather than assuming compatibility
      And its completed command contract is recorded for later resumable runs

    @gap
    Scenario: Partial work is retried from a safe boundary
      Given an article-stage was interrupted before its completion receipt
      When a resumable batch reruns
      Then the stage restarts or resumes according to its checkpoint contract
      And partial output cannot masquerade as complete

    @gap
    Scenario: Force refresh invalidates requested stages deliberately
      Given current artifacts exist
      When the operator requests force refresh for a scope
      Then only the requested scope and dependency-invalidated descendants rerun
      And unrelated current artifacts remain unchanged

  @implemented @network
  Rule: Network-backed work is bounded and observable

    Scenario: Rate limits and retry budgets are honored
      Given multiple public API requests are required
      When the batch runs
      Then configured concurrency, delay, timeout, and retry limits are enforced
      And final failures identify endpoint and article context without exposing secrets

    Scenario: Backfill reports each disposition
      Given multiple articles or episodes are requested
      When backfill completes
      Then updated, unchanged, unavailable, and failed outcomes are distinguishable
      And any failed item makes the command exit non-zero

  @implemented @llm
  Rule: Model-assisted cost remains attributable

    Scenario: Each LLM run records usage and estimated cost
      Given a provider returns usage metadata or token counts can be measured
      When a model-assisted stage completes
      Then input tokens, output tokens, provider, model, and estimated cost are recorded
      And pricing source and assumptions are identifiable

    Scenario: Unknown pricing does not become zero cost
      Given no valid pricing entry exists for the configured model
      When cost is summarized
      Then cost is unknown or unavailable
      And it is not silently recorded as zero

    Scenario: LLM budget exhaustion stops optional calls
      Given a configured batch cost or request budget is reached
      When another model-assisted stage is pending
      Then no new optional call starts
      And the affected stage is reported skipped or unavailable due to budget
      And deterministic completed stages remain valid

  @implemented
  Rule: CLI output supports humans and automation

    Scenario: JSON mode contains no human prose on standard output
      Given a command supports JSON output
      When it succeeds in JSON mode
      Then standard output is one parseable JSON document
      And progress or diagnostics use standard error

    Scenario: Human mode identifies evidence horizon and state
      Given an analysis command succeeds in human mode
      When its report is printed
      Then its report identifies canonical article, analytical level, state, and source horizon
      And it includes inspectable artifact paths or source links where applicable

    Scenario: Failure exit codes are stable
      Given a command fails validation, retrieval, analysis, or partial batch completion
      When it exits
      Then its status is non-zero
      And the failure category is machine-identifiable where JSON mode is supported

  @implemented
  Rule: CI protects deterministic core behavior

    Scenario: Pull-request validation runs the supported Python matrix
      Given a code change is proposed
      When CI runs
      Then tests execute on the supported Python versions
      And deterministic source modules are measured for coverage
      And coverage below the configured floor fails the build

    Scenario: Static viewer contrast is gated
      Given viewer style tokens change
      When CI or release validation runs
      Then the contrast checker passes before publication

  @gap
  Rule: Coverage and end-to-end validation improve without weakening gates

    Scenario: Coverage floor is ratcheted toward core-logic expectations
      Given current tests pass above the existing aggregate floor
      When new deterministic decision behavior is added
      Then focused tests cover its positive, negative, unavailable, and stale paths
      And the aggregate floor is not reduced

    Scenario: Network integration tests use controlled fixtures
      Given public Wikipedia availability is not guaranteed in CI
      When pipeline integration behavior is tested
      Then recorded or fake public responses cover pagination, redirects, retries, and failures
      And an optional live smoke test is separated from deterministic CI
