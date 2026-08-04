@tool @l2
Feature: L2 model-assisted stance trajectory
  Researchers need an auditable estimate of entity-relative stance across article snapshots
  while preserving model uncertainty, prompt contracts, and source text.

  Background:
    Given a complete usable article corpus exists
    And an explicit supported LLM provider and model are configured

  @implemented @llm
  Rule: Stance is measured against explicit focal entities

    Scenario: Focal entities are derived when not supplied
      Given the user does not provide focal entities
      When L2 prepares an analysis
      Then candidate entities are derived from the article's own text or metadata
      And selected entities are persisted in the receipt

    Scenario: User-supplied focal entities override derivation
      Given the user supplies one or more focal entities
      When L2 prepares an analysis
      Then exactly those normalized entities define the stance frame
      And the receipt identifies them

    Scenario: Snapshots preserve source boundaries
      Given article snapshots are selected across time
      When stance prompts are constructed
      Then each prompt identifies the source revision and text boundary
      And the response remains traceable to that snapshot

    Scenario: Stance labels use a bounded schema
      Given the model returns a valid response
      When it is parsed
      Then only documented stance labels, scores, explanations, and quotations are admitted
      And unknown fields or invalid labels fail validation

  @implemented @llm
  Rule: Model instability remains visible

    Scenario: Repeated runs preserve disagreement
      Given the same snapshot is evaluated multiple times under the same prompt contract
      When model outputs disagree beyond the stability contract
      Then the result is marked unstable or unavailable
      And disagreement is not averaged into false certainty

    Scenario: Invalid model output is retried only within bounds
      Given a model response is malformed or schema-invalid
      When parsing fails
      Then the tool may perform a bounded corrective retry
      And final failure produces unavailable with a reason

    Scenario: Prompt contract changes invalidate cached equivalence
      Given a cached L2 result uses a different prompt schema, provider, model, or focal-entity contract
      When reuse is considered
      Then the cached result is not treated as equivalent
      And the contract difference is recorded or recalculation is required

  @implemented
  Rule: Trajectory is separate from isolated labels

    Scenario: Comparable snapshots form a trajectory
      Given at least two usable stance snapshots exist for the same focal entity and contract
      When the trajectory is computed
      Then ordered scores and labels retain their revision timestamps
      And change magnitude is reported with source endpoints

    Scenario: Incomparable snapshots fail closed
      Given snapshots differ in focal entity, prompt contract, or unusable source coverage
      When trajectory computation is attempted
      Then they are not combined into one stance change
      And the reason is reported

    Scenario: Exact-event alignment is explicit
      Given a fresh L1 event is available
      When L2 selects event-relative snapshots
      Then the before and after revisions are tied to exact event boundaries
      And candidate-relative or static analyses use different mode labels

  @implemented @policy
  Rule: Stance output is not a neutrality verdict

    Scenario: Stance wording remains model-relative
      Given a usable stance trajectory exists
      When it is reported
      Then the report says the configured model classified entity-relative language under a named prompt contract
      And it does not declare the article neutral, biased, true, false, or compliant with Wikipedia policy

    Scenario: Source text is available for human adjudication
      Given a stance label or score is displayed
      When a researcher inspects its evidence
      Then supporting quotations or linked snapshot text are available where the contract permits
      And a researcher can inspect the underlying revision

  @gap
  Rule: L2 requires stronger calibration before comparative claims

    Scenario: Stance calibration uses neutral and known-change controls
      Given an L2 release is evaluated
      When calibration runs
      Then stability, inter-run disagreement, directional accuracy, and false-positive rates are measured
      And model or prompt changes cannot silently inherit prior calibration

    Scenario: Section-aware stance awaits a stable segmentation contract
      Given article-wide stance may hide section-specific changes
      When section-level analysis is proposed
      Then section identity and cross-revision alignment are specified and benchmarked first
      And article-wide output does not imply section-level coverage
