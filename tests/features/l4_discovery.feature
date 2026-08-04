@tool @l4
Feature: L4 graph-guided discovery
  Researchers need confirmed public-account overlap to prioritize new article candidates
  while independent content evidence remains the only route to confirmation.

  Background:
    Given one or more seed articles have fresh exact L1 events
    And eligible public registered accounts have supported event attribution

  @implemented @network
  Rule: Seed eligibility fails closed

    Scenario: A confirmed seed contributes eligible accounts
      Given a seed has current exact confirmation and current attribution
      When discovery begins
      Then only supported registered non-bot accounts from exact event evidence enter the seed set
      And account provenance retains seed article, event, and public revision links

    Scenario Outline: Ineligible seed evidence is excluded
      Given the seed evidence is <state>
      When graph construction runs
      Then it contributes no discovery edge
      And the exclusion reason is reported

      Examples:
        | state                       |
        | coarse candidate only       |
        | exactly rejected            |
        | stale                       |
        | schema-incompatible         |
        | anonymous IP actor          |
        | bot actor                   |
        | hidden or unavailable actor |

  @implemented @network
  Rule: The graph prioritizes rather than judges

    Scenario: Public contributions produce candidate page edges
      Given an eligible seed account has public contributions to other pages
      When contributions are retrieved
      Then account-to-page edges retain public source evidence
      And candidate pages are ranked by documented overlap and activity features

    Scenario: Canonical aliases collapse before ranking
      Given discovered page titles redirect to the same canonical page
      When candidates are normalized
      Then they form one candidate identity
      And evidence from aliases is retained without duplicate ranking rows

    Scenario: Seed pages and explicit exclusions do not re-enter candidates
      Given a discovered page is already a seed or matches a configured exclusion
      When candidates are assembled
      Then it is excluded with a reason

    Scenario: Graph score cannot be reported as content evidence
      Given a candidate has a high account-overlap score
      When the candidate is reported before retest
      Then it is labeled a discovery candidate
      And the score is not called drift, bias, coordination, or confirmation

  @implemented @network
  Rule: Every discovered page receives an independent content retest

    Scenario: Candidate retest starts from canonical article content
      Given a ranked discovered candidate
      When it is evaluated
      Then its own canonical L0 corpus is collected or opened
      And its own deterministic L1 scan and exact confirmation run independently
      And seed graph features are not input to the L1 decision

    Scenario Outline: Retest outcomes remain distinct
      Given independent retest produces <outcome>
      When discovery results are persisted
      Then the candidate state is <display>

      Examples:
        | outcome        | display       |
        | confirmed      | confirmed     |
        | not_confirmed  | not_confirmed |
        | healthy        | healthy       |
        | unavailable    | unavailable   |

    Scenario: Only exact retest confirmation counts as an L4 finding
      Given graph overlap exists
      But independent exact L1 does not confirm an event
      When aggregate discovery findings are computed
      Then the page is not counted as graph-discovered confirmed drift

  @implemented @policy
  Rule: Discovery does not infer coordination or identity

    Scenario: Shared public accounts are framed as overlap
      Given the same literal public account edited seed and candidate pages
      When the relationship is described
      Then it is called observed public-account overlap
      And it does not establish common ownership, off-wiki identity, coordination, motive, or misconduct

    Scenario: Separate account names remain separate graph nodes
      Given two account names might refer to the same person
      When the graph is built
      Then they remain separate nodes
      And no identity resolution is attempted

  @gap
  Rule: Expansion remains one hop until separately validated

    Scenario: Multi-hop snowball discovery is disabled by default
      Given a candidate is independently confirmed
      When the current discovery run completes
      Then it is not automatically used as a new seed in the same run
      And any future recursive expansion requires explicit depth, budget, deduplication, and false-positive controls
