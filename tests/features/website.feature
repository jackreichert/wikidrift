@website
Feature: WikiDrift public research website
  Readers, researchers, and Wikipedia contributors need a cautious static website
  that turns committed findings into inspectable research leads without implying bias or bad faith.

  Background:
    Given the site is generated from committed findings
    And no token corpus, API key, or LLM key is required for the build

  @implemented @policy
  Rule: Publication preserves epistemic state

    Scenario: Every article carries a non-accusatory disclaimer
      Given an article is published
      When a reader opens its page
      Then the page describes something to inspect
      And the page does not characterize the article or its editors as biased, malicious, coordinated, or acting in bad faith

    Scenario Outline: Distinct rewrite states remain distinct
      Given the authoritative rewrite state is <state>
      When the article page is generated
      Then the Rewrite panel describes the result as <display>
      And it does not substitute <forbidden>

      Examples:
        | state         | display                              | forbidden                    |
        | confirmed     | an exactly confirmed rewrite event   | a coarse candidate only      |
        | not_confirmed | an exact candidate rejection          | healthy                      |
        | descriptive_anomalies | anomalies below the exact-check floor | healthy                |
        | healthy       | no candidate under current thresholds | not_confirmed                |
        | unavailable   | unavailable with a reason              | no candidate found           |

    Scenario: Stale or incompatible evidence is withheld
      Given a finding does not match the current schema, threshold contract, endpoint receipt, or corpus horizon
      When the site is built with a local corpus
      Then the finding cannot influence a headline, badge, summary, or evidence panel
      And the trust report records the article, artifact kind, status, and reason
      And the article displays unavailable rather than healthy

    Scenario: A frozen build remains reproducible
      Given no local corpus exists
      And committed findings contain compatible self-describing receipts
      When the static site is built
      Then the build succeeds without network access
      And published evidence retains its saved horizon and source links

  @implemented
  Rule: Readers can discover and prioritize articles

    Scenario: Findings are searchable
      Given the findings index contains published articles
      When a reader enters text from an article title, category, summary, or indexed finding
      Then only matching articles remain visible
      And the result count updates
      And a zero-result message appears when nothing matches

    Scenario: Findings can be filtered by category
      Given category filter controls are visible
      When a reader selects a category
      Then only articles in that category remain visible
      And exactly one category control is marked pressed
      And pagination returns to the first page

    Scenario Outline: Findings can be sorted
      Given multiple findings are visible
      When the reader selects <sort>
      Then findings are ordered by <ordering>
      And title is the deterministic tie breaker

      Examples:
        | sort                    | ordering                      |
        | article title           | title ascending               |
        | category                | category then title ascending |
        | largest rewrite         | PWR mass descending           |
        | largest vocabulary shift| lexical divergence descending |
        | strongest signal        | composite display score descending |

    Scenario: Findings are paginated without losing filters
      Given more than 25 articles match the active search and category
      When the reader moves to the next page
      Then at most 25 matching articles are visible
      And the active search, category, and sort remain applied
      And previous and next controls expose disabled states at their boundaries

    Scenario: Signal badges are plain-language cues
      Given an article has multiple trusted evidence families
      When its finding card is generated
      Then no more than the configured badge limit is displayed
      And each badge names an observable signal or evidence state
      And no badge claims bias, truth, intent, or misconduct

  @implemented
  Rule: Article pages reveal evidence progressively

    Scenario: Overview and Rewrite are always present
      Given any published article
      When its page is generated
      Then an Overview tab is present
      And a Rewrite tab is present
      And the overview identifies available and unavailable evidence families

    Scenario Outline: Optional tabs appear only with usable evidence
      Given <evidence> is available and admitted by trust policy
      When the article page is generated
      Then the <tab> tab is present

      Examples:
        | evidence                         | tab        |
        | lexical comparison               | Vocabulary |
        | citation-source comparison       | Citations  |
        | usable cross-language framing    | Framing    |
        | fact divergence results          | Facts      |
        | revision or framing receipts     | Versions   |

    Scenario Outline: Optional tabs do not imply missing analysis ran
      Given <evidence> is absent, failed, withheld, or unusable
      When the article page is generated
      Then the <tab> tab is absent
      And the overview reports that evidence family as unavailable when applicable

      Examples:
        | evidence                    | tab        |
        | lexical comparison          | Vocabulary |
        | citation-source comparison  | Citations  |
        | cross-language framing      | Framing    |
        | fact divergence results     | Facts      |
        | revision receipts           | Versions   |

    Scenario: Tabs support direct links and browser history
      Given an article has multiple tabs
      When a reader opens a valid tab hash
      Then that tab and panel become active
      And when the reader uses browser back or forward
      Then the active tab follows the URL hash

    Scenario: Invalid tab hashes fail safely
      Given an article page receives an unknown hash
      When tab activation runs
      Then the first tab is active
      And exactly one tab is keyboard-focusable

    Scenario: In-page evidence links activate their target tab
      Given an overview link points to an available evidence tab
      When the reader activates the link
      Then the target panel becomes active
      And focus moves to its tab

  @implemented
  Rule: Rewrite evidence is complete and honest

    Scenario: Every Rewrite panel includes the interval chart structure
      Given an article page is generated
      When the reader opens Rewrite
      Then the Persistence-weighted change by interval figure is present
      And the axis includes 0%, the 25% candidate floor, 50%, 75%, and 100%
      And the detector path explains coarse scan, interval scoring, and exact checking

    Scenario: Measured intervals render measured values
      Given interval profile receipts exist
      When the chart is rendered
      Then each interval row identifies its end date, PWR loss, standing gain, paired-change lead, removed and added PWR mass, and state
      And a sub-floor anomaly is marked descriptive rather than excluded or healthy
      And a replacement lead is not presented as confirmed semantic replacement
      And a candidate row reports whether exact checking confirmed or rejected it

    Scenario: Missing interval receipts remain inside the chart
      Given no interval profile receipt exists
      When the chart is rendered
      Then the normal axis and plot frame remain visible
      And a row displays Data missing
      And no measured bar or value is fabricated
      And the reason explains how the data can be refreshed

    Scenario: Confirmed events show exact receipts
      Given one or more exact events are confirmed
      When Rewrite is rendered
      Then each event identifies exact before and after revision links
      And it displays durable-spine drop, PWR mass, duration, and corpus horizon when available
      And exact confirmation overrides any legacy coarse candidate presentation

    Scenario: Supporting analyses can switch between confirmed events
      Given multiple confirmed events have vocabulary, citation, framing, or fact results
      When a reader opens a supporting evidence tab with multiple events
      Then an event selector identifies each exact revision pair
      And selecting an event displays only that event's result
      And an unavailable event does not hide a completed sibling event

    Scenario: Rejected candidates remain inspectable
      Given a coarse candidate was exactly checked and rejected
      When Rewrite is rendered
      Then the candidate window and coarse measurement remain visible
      And the exact outcome is Rejected candidate window
      And the article is not presented as confirmed

    Scenario: Attribution wording describes action rather than intent
      Given exact-event attribution is available
      When the receipt is rendered
      Then it describes accounts associated with removals and origin authors of surviving replacement text
      And raw counts support every displayed share
      And it states that the receipt does not establish bias, motive, coordination, or misconduct

    Scenario: Process evidence cannot change confirmation
      Given editorial-process context is available
      When it is rendered
      Then revision, revert, talk, protection, and page-operation items retain public links
      And each evidence family distinguishes observed, not observed, and unavailable
      And the context remains separate from the exact content decision

  @implemented
  Rule: Supporting evidence remains inspectable

    Scenario: Vocabulary comparisons disclose their basis
      Given a trusted lexical artifact exists
      When Vocabulary is rendered
      Then the comparison span and interval source are described
      And before and after token counts are displayed when available
      And terms used more and less are filtered for readable prose
      And divergence is described in plain language without implying meaning or bias

    Scenario: Citation changes rate no source
      Given a trusted citation-source artifact exists
      When Citations is rendered
      Then domains and citation types show before-to-after counts
      And added and dropped domains are separately listed
      And the page states that it does not classify sources as trustworthy or untrustworthy

    Scenario: Framing differences invite comparison rather than judgment
      Given a usable cross-language framing result exists
      When Framing is rendered
      Then each divergence names its languages, verdict, and supporting quotations when present
      And temporal results identify exact, candidate-relative, or static mode
      And the page states that differences do not determine which edition is right

    Scenario: Failed framing is not rendered as agreement
      Given framing retrieval or adjudication failed
      When the article page is generated
      Then no no-difference claim is shown
      And the evidence is absent or unavailable

    Scenario: Fact comparisons preserve insufficient evidence
      Given fact comparison results exist
      When Facts is rendered
      Then contradiction, compatible difference, agreement, and insufficient evidence remain distinct
      And each result retains its question and explanatory note

    Scenario: Version receipts link to source revisions
      Given version records exist
      When Versions is rendered
      Then each row identifies language, comparison point, localized title, revision, timestamp, and text boundary when available
      And each revision links to its public Wikipedia oldid

    Scenario: Candidate redlines preserve provenance
      Given an L3 redline export exists
      When the reader opens its candidate page
      Then before and after prose are distinguishable
      And additions and removals are visible without relying on color alone
      And candidate status and exact outcome are shown
      And author coloring is accompanied by a text legend

  @implemented @policy
  Rule: The site remains accessible and responsive

    Scenario: Article tabs are keyboard operable
      Given focus is on a tab
      When the reader presses an arrow key, Home, or End
      Then focus moves according to the tab pattern
      And the matching panel becomes active
      And selected and tabindex attributes remain synchronized

    Scenario: Status never relies on color alone
      Given a colored badge, chart row, diff, or signal card is displayed
      When a reader inspects the status
      Then a visible text label communicates the same state

    Scenario: Tables and figures expose semantic structure
      Given evidence is rendered in a table or chart
      When assistive technology inspects the evidence
      Then column and row headers use semantic scope where applicable
      And figures have accessible names or captions
      And interactive controls have accessible names and state

    Scenario: Mobile navigation exposes its state
      Given the mobile navigation control is visible
      When the reader toggles it
      Then navigation links open or close
      And aria-expanded and the control label reflect the state

    Scenario: Enlarged process diagrams restore focus
      Given a rendered process diagram can be enlarged
      When the reader opens and closes its dialog by button, backdrop, or Escape
      Then focus moves into the dialog on open
      And focus returns to the enlarge control on close
      And background scrolling is restored

    Scenario: The configured palette passes automated contrast checks
      Given the viewer color token pairs
      When the contrast checker runs
      Then every configured normal-text pair meets 4.5 to 1
      And every configured UI or large-text pair meets 3 to 1

  @implemented
  Rule: Static generation is deterministic and auditable

    Scenario: A normal build emits the complete site shell
      When the viewer build completes
      Then it writes home, findings, summary, methodology, glossary, article pages, assets, CNAME, and no-Jekyll output
      And it writes machine-readable and human-readable trust reports
      And generated article slugs cannot escape the article output directory

    Scenario: Curated categories override model-assisted categories
      Given an article has both a curated category and a cached model category
      When categories are resolved
      Then the curated category is used

    Scenario: Unknown cached categories fail closed
      Given a cached category is not in the approved category set
      When categories are resolved
      Then the cache value is rejected
      And the article receives a valid fallback category

  @gap
  Rule: Browser behavior requires executable acceptance coverage

    Scenario: Core website workflows are tested in desktop and mobile browsers
      Given the static site has been built
      When browser acceptance tests run at representative desktop and mobile viewports
      Then search, filter, sort, pagination, tabs, hashes, dialogs, and mobile navigation pass
      And no content overlaps or creates unintended horizontal page overflow
      And keyboard-only operation completes each primary workflow
