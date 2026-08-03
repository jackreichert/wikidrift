@layers @l4 @living-documentation
Feature: Discover other articles worth testing
  As a researcher with a confirmed rewrite
  I want public attribution to guide further searches
  So that related articles are independently tested rather than presumed related

  Rule: L4 uses attribution only as a search prior

    Scenario: Build a discovery lead from fresh evidence
      Given an article has a fresh exact L1 confirmation
      And structured public-account attribution is available
      When L4 builds a discovery graph
      Then each graph account is shown by its literal public name
      And each graph relationship is supported by a confirmed event receipt
      And bots, anonymous addresses, and hidden names are excluded
      And accounts are not merged into inferred real-world identities

    Scenario: Exclude stale or incomplete evidence
      Given an attribution artifact does not match its corpus horizon or threshold contract
      When L4 builds a discovery graph
      Then that artifact is excluded with a reason
      And it contributes no discovery edge

  Rule: Every discovered article must earn its own finding

    Scenario: Promote an independently confirmed candidate
      Given public activity identifies another article worth inspecting
      When that article completes its own L1 analysis
      And its durable rewrite is exactly confirmed
      Then it may appear as a confirmed rewrite lead
      And its finding cites its own content trajectory and revisions

    Scenario: Do not promote by association
      Given an article shares an editor with a confirmed event
      But its own L1 analysis is coarse, rejected, unavailable, or not run
      When L4 reports discovery results
      Then the article is not described as a confirmed rewrite
      And editor overlap is not described as proof of coordination or intent
