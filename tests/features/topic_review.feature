@website @topic-review
Feature: Reviewing a topic on its article page
  A reader opens one topic's article page and decides what to inspect from what the page shows
  and says. Every statement on that page must agree with the data behind it (the L1 confirmation,
  rewrite_status, redline pivots, framing, lexical, citation and fact artifacts) and with the other
  tabs on the same page. The page must preserve evidence states from data to headline: not run,
  candidate, confirmed, withheld and unavailable are distinct, and missing coverage is never turned
  into a finding or into a negative finding.

  These scenarios specify what WikiDrift is supposed to do. They run against synthetic fixtures only
  (no token corpus, no network, no LLM) through viewer/build.py. Scenarios tagged @bug describe the
  intended behavior and FAIL against current behavior; they are executed as expected failures, so CI
  stays green while the bug is open and goes red ("unexpected success") the moment it is fixed, at
  which point the tag must be removed. Never weaken a @bug scenario to make it pass.

  Tag @smell_<id> links a scenario to the mechanical smell taxonomy in docs/article-qa-checklist.md
  section 11 (confirmed_without_pivots, framing_langs_mismatch,
  framing_claims_confirmed_while_rewrite_withheld, framing_mode_mismatch, withheld_copy_mismatch).

  Background:
    Given the site is generated from committed findings
    And no token corpus, API key, or LLM key is required for the build

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: The Rewrite tab agrees with the confirmation and rewrite_status data

    Scenario Outline: The recorded rewrite state decides the Rewrite heading and the Overview signal
      Given a topic whose rewrite data is <data>
      When the topic page is generated
      Then the Rewrite panel heading is "<heading>"
      And the Overview rewrite signal reads "<signal>"
      And the page summary does not contradict that state

      Examples:
        | data                                                      | heading                                    | signal                        |
        | an exact confirmation with 2 confirmed episodes           | 2 confirmed rewrite episodes               | 2 confirmed                   |
        | an exact confirmation that rejected its candidate         | No candidate rewrite window was confirmed  | No candidate window confirmed |
        | a stale exact confirmation                                | Rewrite analysis needs refresh             | Rewrite analysis needs refresh |
        | an exact confirmation withheld for a missing endpoint     | Exact rewrite verdict is withheld          | Exact rewrite verdict is withheld |
        | a confirmation with too few snapshots                     | Too few snapshots for rewrite analysis     | Too few snapshots for rewrite analysis |
        | no confirmation and rewrite_status none                   | No candidate rewrite window was found      | No candidate window found     |
        | no confirmation and no rewrite_status                     | Rewrite analysis is not available          | Rewrite analysis is not available |

    Scenario: Confirmed episode counts agree everywhere on the page
      Given a topic with an exact confirmation of 3 confirmed episodes and 3 exported redlines
      When the topic page is generated
      Then the page summary, the Overview signal and the Rewrite heading all report 3 confirmed episodes
      And the exact outcomes table lists 3 candidate windows, each marked confirmed
      And exactly 3 "View redline" links are shown

    Scenario: Rejected candidates are never presented as confirmed
      Given an exact confirmation that rejected one candidate with a 10.0% durable-spine drop against a 20.0% requirement
      When the topic page is generated
      Then the Rewrite panel shows the candidate window, its coarse measurement and "Rejected: 10.0% durable-spine drop, below the required 20.0%"
      And the page does not say "Confirmed" about that window anywhere
      And the page summary and Overview do not describe a confirmed rewrite

    Scenario: Mixed exact outcomes are counted from the candidate data
      Given an exact confirmation that sent 3 candidate windows to exact checking: 1 confirmed and 2 rejected
      When the topic page is generated
      Then the candidate summary reads "1 confirmed, 2 rejected"
      And the page reports 1 confirmed rewrite episode

    @bug
    Scenario: A healthy coarse scan is not described as a rejected candidate
      Given an exact confirmation whose coarse verdict is HEALTHY and which sent no candidate to exact checking
      When the topic page is generated
      Then the Rewrite panel heading is "No candidate rewrite window was found"
      And the Overview rewrite signal reads "No candidate window found"
      And neither the Rewrite panel nor the Overview says exact checking did not confirm a rewrite

    @bug
    Scenario: rewrite_status unavailable outranks a confirmation file that still says confirmed
      Given a confirmation file with 2 confirmed episodes
      And rewrite_status records the topic as unavailable with reason "stale exact confirmation"
      When the topic page is generated
      Then the Rewrite panel says the rewrite analysis needs refresh
      And no confirmed episode, "View redline" link or confirmed headline is shown

    @bug
    Scenario: rewrite_status unavailable outranks leftover redline pivots
      Given redline pivots remain on disk for a topic
      And rewrite_status records the topic as unavailable with reason "stale exact confirmation"
      And no confirmation file is present
      When the topic page is generated
      Then the Rewrite panel says the rewrite analysis needs refresh
      And no candidate window, redline link or rewrite headline is shown

    @bug
    Scenario: rewrite_status finding without any pivots does not read as a finding
      Given rewrite_status records the topic as finding
      And no pivots file exists for it
      When the topic page is generated
      Then the page is still generated
      And the Rewrite panel reads as unavailable
      And no candidate window or redline link is shown

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: Withheld or unavailable results never read as a finding or as no finding

    Scenario Outline: A withheld confirmation hides its episodes and states no verdict
      Given a confirmation that is unavailable because "<reason>"
      And its measured intervals and evaluated candidates are still listed
      When the topic page is generated
      Then the Rewrite panel does not claim a confirmed rewrite episode
      And it does not say that no candidate rewrite window was found or confirmed
      And it does not call the article healthy
      And no "View redline" link is shown
      And the detector path marks the exact check as Unavailable

      Examples:
        | reason                                               |
        | stale exact confirmation                             |
        | artifact withheld: stable endpoint receipt is missing |
        | loaded 32 of 42 expected snapshots                    |
        | too few snapshots                                    |

    Scenario Outline: Withheld measured intervals are labelled as withheld
      Given a withheld confirmation with a measured interval of <loss>% loss
      When the topic page is generated
      Then the interval row is not labelled "no anomaly threshold crossed"
      And the chart says the bars neither confirm nor rule out a rewrite
      And the Rewrite panel says the withholding is not a finding that no rewrite occurred

      Examples:
        | loss |
        | 15.0 |
        | 28.5 |
        | 61.0 |

    @smell_withheld_copy_mismatch
    Scenario Outline: Each recorded unavailable reason gets its own plain explanation
      Given rewrite_status records the topic as unavailable with reason "<reason>"
      When the topic page is generated
      Then the Rewrite panel heading is "<heading>"
      And the Overview shows the same heading for its Rewrite signal
      And the Rewrite panel does not present a completed negative result

      Examples:
        | reason                                                | heading                                         |
        | stale exact confirmation                              | Rewrite analysis needs refresh                  |
        | artifact withheld: stable endpoint receipt is missing | Exact rewrite verdict is withheld               |
        | too few snapshots                                     | Too few snapshots for rewrite analysis          |
        | loaded 32 of 42 expected snapshots                    | Rewrite analysis has incomplete source coverage |
        | candidate artifact could not be materialized          | Before-and-after evidence is unavailable        |

    @bug @smell_withheld_copy_mismatch
    Scenario Outline: Reasons written by the redline export are not flattened into generic copy
      Given rewrite_status records the topic as unavailable with reason "<reason>"
      When the topic page is generated
      Then the Rewrite panel heading is not the generic "Rewrite analysis is not available"
      And the Rewrite panel explains the recorded reason class

      Examples:
        | reason                                                      |
        | candidate pair could not be materialized: 11->12            |
        | legacy coarse pivot lacks compatible evidence receipt       |

    Scenario: A withheld topic is listed as not available on the Overview
      Given a confirmation that is unavailable because "artifact withheld: stable endpoint receipt is missing"
      When the topic page is generated
      Then the Overview coverage note lists Rewrite under not available in this export
      And it states that missing coverage is not a negative finding
      And the page summary does not mention a confirmed rewrite

    @bug
    Scenario: A topic with only withheld evidence is not summarised as mild change
      Given a confirmation that is unavailable because "artifact withheld: stable endpoint receipt is missing"
      And no other evidence exists for the topic
      When the topic page is generated
      Then the page summary does not say only mild change shows up
      And the page summary says the rewrite check could not be completed

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: Confirmed episodes always show redline links and pivot pages

    Scenario: Each confirmed episode links to a redline page that exists
      Given a topic with 2 confirmed episodes and a redline pivot for each
      When the whole site is built
      Then each confirmed episode shows a "View redline" link
      And each link targets a redline page written by the build
      And the 2 links target 2 different redline pages

    Scenario Outline: Redlines are matched by candidate window or by exact revision pair
      Given a confirmed episode whose redline pivot is keyed by <key>
      When the topic page is generated
      Then the episode shows "View redline" for that pivot
      And it does not show "Redline unavailable"

      Examples:
        | key                                              |
        | the same candidate window dates                  |
        | its exact before and after revision ids only     |

    Scenario: A confirmed episode without an exported redline says so and never links to a dead page
      Given a topic with 2 confirmed episodes
      And a redline pivot exists for only one of them
      When the topic page is generated
      Then the exported episode shows "View redline"
      And the other episode shows "Redline unavailable"
      And no link on the page targets a redline page that is not written

    @bug @smell_confirmed_without_pivots
    Scenario Outline: A confirmed topic is not published as a finding without any redline
      Given a confirmation with 3 confirmed episodes
      And no redline pivots are exported for the topic
      And <status>
      When the topic page is generated
      Then the page either withholds the confirmed claim or shows a redline link for every confirmed episode
      And the page never shows a confirmed headline next to only "Redline unavailable"

      Examples:
        | status                                                           |
        | rewrite_status has no entry                                      |
        | rewrite_status records unavailable "stale exact confirmation"    |

    Scenario: A control topic with a finding and its redlines stays clean
      Given a confirmed topic with 3 pivots and rewrite_status finding
      When the whole site is built
      Then the Rewrite tab, the Overview and the findings list agree on the 3 confirmed episodes
      And the 3 redline pages written equal the 3 pivots

    Scenario: Redline page counts equal pivot counts
      Given a topic with 4 candidate redline pivots and no exact confirmation
      When the whole site is built
      Then 4 redline pages are written and 4 are linked from the Rewrite tab
      And the Overview lists the same 4 candidate windows

    Scenario: A redline page identifies its evidence state and links back
      Given redline pivots with status confirmed and status rejected
      When their redline pages are generated
      Then the confirmed page is titled as a confirmed candidate redline and says it is the coarse window, not the exact pair
      And the rejected page is titled as a rejected candidate redline
      And both link back to the topic page

    Scenario: Coarse candidates without exact confirmation are never called confirmed
      Given a pivot with no recorded status and no exact confirmation
      When the topic page is generated
      Then the Rewrite panel and the Overview call it a candidate rewrite window
      And the page does not say confirmed

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: Interval metrics are intervals, not edits, and are distinct from durable-spine drop

    Scenario: Every interval row is labelled with its span and the chart says it is not an edit
      Given measured intervals of 181 days and 365 days
      When the topic page is generated
      Then each row shows its end date and its day span, such as "181-day interval"
      And the chart caption says each bar is the loss across one snapshot interval, not a single edit
      And the caption says the metric differs from the durable-spine drop

    Scenario: A 61% interval loss is never presented as a durable-spine drop or an edit
      Given a candidate window with a peak interval loss of 61.0% and a durable-spine drop of 87.7%
      When the topic page is generated
      Then the interval row reads "Loss 61.0%"
      And the 87.7% appears only as a durable-spine drop for the whole window
      And no text calls 61% a durable-spine drop, a rewrite share, or the share of the article rewritten
      And the peak is described as persistence-weighted loss

    Scenario: Chart values equal the interval data
      Given an interval with 42.5% loss, 12345 removed and 6789 added persistence-weighted units
      When the topic page is generated
      Then the bar width is 42.50% and the value reads "Loss 42.5%"
      And the mass reads "12,345 removed · 6,789 added PWR"

    Scenario Outline: Missing gain fields are not shown as zero
      Given an interval with <recorded> gain fields
      When the topic page is generated
      Then the row shows "<added>"

      Examples:
        | recorded      | added                      |
        | no            | added PWR not recorded     |
        | zero-valued   | 0 added PWR                |
        | positive      | 16,959 added PWR           |

    Scenario Outline: Elevated loss never reads as no anomaly
      Given a <status> confirmation with an interval loss of <loss>%
      When the topic page is generated
      Then the row is not labelled "no anomaly threshold crossed"

      Examples:
        | status        | loss |
        | confirmed     | 15.0 |
        | not_confirmed | 28.5 |
        | unavailable   | 61.0 |

    Scenario: The detector path interval count equals the rows drawn
      Given a confirmation with 5 measured intervals
      When the topic page is generated
      Then the detector path says "5 intervals scored"
      And the chart draws exactly 5 interval rows

    Scenario: Intervals inside a confirmed window carry the exact verdict and others do not
      Given a confirmed candidate window covering two of three measured intervals
      When the topic page is generated
      Then the two covered rows read "Confirmed candidate window" with the window dates
      And the third row does not read as part of a candidate window

    Scenario: A micro exact pair is flagged instead of presented as the window localisation
      Given a confirmed 18-month window whose exact pair carries 0.5% of the window drop
      When the topic page is generated
      Then the candidate outcome says the pair is not a localisation
      And it does not present the pair as "Exact pair"

    Scenario: A missing interval profile keeps the chart frame and invents nothing
      Given a confirmation without any interval profile
      When the topic page is generated
      Then the axis and plot frame remain visible
      And one row reads "Data missing"
      And no bar or value is shown

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: The Framing tab agrees with the framing data and the rewrite verdict

    @smell_framing_langs_mismatch
    Scenario Outline: Languages compared match the framing data for every event
      Given a framing artifact with <events> whose editions are <editions>
      When the topic page is generated
      Then each event's "Languages compared" line lists exactly its editions in order
      And, for a single comparison, the Versions tab lists only those editions

      Examples:
        | events                    | editions                                      |
        | a single comparison       | en, fr, de                                    |
        | two events                | en, ar, sr; and en, ja, id                    |
        | two events out of order   | en, vi, ro; and en, ja (the later one listed first) |

    Scenario: Version receipts equal the framing snapshots
      Given a temporal framing artifact with before and after revisions for en and fr
      When the topic page is generated
      Then the Versions tab has one before and one after row per compared edition
      And each row links to the revision id recorded in the framing snapshots

    Scenario: Candidate-relative framing is labelled as a candidate window, not a confirmed rewrite
      Given a candidate_relative framing artifact
      When the topic page is generated
      Then the Framing tab says the comparison is at the endpoints of an L1 candidate window
      And it does not say confirmed rewrite

    Scenario: Pivot-relative framing may say confirmed rewrite when the exact result supports it
      Given a pivot_relative framing artifact for a window that an exact confirmation confirms
      When the topic page is generated
      Then the Framing tab says the comparison is at the endpoints of the confirmed rewrite
      And the stated window equals the confirmed episode's candidate window

    @bug @smell_framing_claims_confirmed_while_rewrite_withheld
    Scenario Outline: Framing does not sell a confirmed rewrite while the rewrite verdict is withheld
      Given a pivot_relative framing artifact
      And the rewrite verdict is withheld because "<reason>"
      When the topic page is generated
      Then the Framing tab does not say "confirmed rewrite"
      And it says the comparison is not tied to a currently confirmed rewrite

      Examples:
        | reason                                                |
        | stale exact confirmation                              |
        | artifact withheld: stable endpoint receipt is missing |

    @bug @smell_framing_mode_mismatch
    Scenario Outline: Pivot-relative framing needs a confirmed rewrite
      Given a pivot_relative framing artifact
      And the exact result is <result>
      When the topic page is generated
      Then the Framing tab does not say "confirmed rewrite"

      Examples:
        | result                                    |
        | not_confirmed with a rejected candidate   |
        | not_confirmed after a healthy coarse scan |

    @bug @smell_framing_mode_mismatch
    Scenario: Pivot-relative framing for a different window than any confirmed episode is not confirmed
      Given a pivot_relative framing artifact for the window 2015-01-01 to 2016-01-01
      And the exact confirmation confirms only the window 2010-01-01 to 2011-01-01
      When the topic page is generated
      Then the Framing tab does not describe 2015-01-01 to 2016-01-01 as a confirmed rewrite

    @bug @smell_framing_mode_mismatch
    Scenario Outline: Static framing does not claim an endpoint comparison
      Given a framing artifact with mode <mode> that records a pivot window
      When the topic page is generated
      Then the Framing tab does not say it compared historical lead revisions at the endpoints of a window

      Examples:
        | mode                 |
        | static               |
        | pivot_informed_static |
        | (no mode recorded)   |

    Scenario Outline: Failed or insufficient framing is absent and not a no-difference result
      Given a framing artifact that <state>
      When the topic page is generated
      Then there is no Framing tab
      And the Overview lists Framing as not available
      And the page never says "No clear differences"

      Examples:
        | state                                          |
        | recorded a provider error                      |
        | made no model call and was insufficient        |
        | has an unavailable result for its only event   |

    Scenario: A completed comparison with no divergences is a valid no-difference result
      Given a framing artifact with one model call and no divergences
      When the topic page is generated
      Then the Framing tab says no clear differences were recorded
      And the Overview shows no "clear differences" signal

    @bug
    Scenario Outline: Overview framing counts equal the Framing tab rows
      Given a framing artifact with <rows> divergence rows of which <contradict> contradict
      When the topic page is generated
      Then the Framing table has <rows> rows
      And the Overview signal reads "<signal>"

      Examples:
        | rows | contradict | signal                         |
        | 1    | 0          | 1 clear difference             |
        | 3    | 0          | 3 clear differences            |
        | 3    | 2          | 3 clear differences · 2 contradict |

    @bug
    Scenario Outline: The Overview only says framing compared around the rewrite window for temporal modes
      Given a framing artifact with mode <mode>
      When the topic page is generated
      Then the Overview framing note <expectation>

      Examples:
        | mode               | expectation                                  |
        | pivot_relative     | says it compared around the rewrite window   |
        | candidate_relative | says it compared around the rewrite window   |
        | static             | does not mention the rewrite window          |

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: The Overview text matches the tabs

    Scenario Outline: Available and unavailable evidence lists match the tabs present
      Given a topic whose evidence is <evidence>
      When the topic page is generated
      Then the tabs present are <tabs>
      And the Overview lists exactly the evidence families with tabs as available, with Rewrite following its state
      And every other family is listed under not available in this export

      Examples:
        | evidence                                   | tabs                                               |
        | only an exact confirmation                 | Overview, Rewrite                                  |
        | a confirmation and lexical                 | Overview, Rewrite, Vocabulary                      |
        | lexical, citations, facts and framing      | Overview, Rewrite, Vocabulary, Citations, Framing, Facts, Versions |
        | a withheld confirmation and framing        | Overview, Rewrite, Framing                         |

    Scenario: Every in-page link from the Overview targets an existing tab
      Given a topic with confirmed episodes, vocabulary, citations, facts and framing
      When the topic page is generated
      Then every "#" link in the Overview targets a tab panel on the same page

    @bug
    Scenario: The Overview wording signal agrees with a Vocabulary tab built from several events
      Given a lexical artifact with events whose divergence is 0.50
      When the topic page is generated
      Then the Vocabulary tab says the wording changed a lot
      And the Overview wording signal also says the wording changed a lot
      And the page summary does not say only mild change shows up

    @bug
    Scenario: Exact rejection is not listed as no candidate found
      Given an exact confirmation that rejected its candidate
      When the topic page is generated
      Then the Overview coverage note does not label Rewrite as "no candidate found"
      And the Overview signal says exact checking did not confirm a durable rewrite

    Scenario: Footnote signal counts equal the Citations tab
      Given a citation artifact with 3 domains added and 1 dropped
      When the topic page is generated
      Then the Overview reads "3 sites grew · 1 shrank"
      And the Citations tab lists 3 added and 1 dropped domains

    Scenario: Fact signal counts equal the Facts tab verdicts
      Given a fact artifact with 1 contradiction, 2 compatible differences, 3 agreements and 1 insufficient
      When the topic page is generated
      Then the Overview reads "1 contradiction · 2 compatible differences · 3 agree · 1 not enough"
      And the Facts tab reports the same four counts and lists 7 questions with those verdicts
      And the page summary says "1 factual question out of 7 contradicts across languages"

    Scenario: Candidate windows on the Overview equal the Rewrite tab
      Given a topic with 2 candidate redline pivots and no exact confirmation
      When the topic page is generated
      Then the Overview lists 2 candidate windows with their dates and links
      And the Rewrite tab lists the same 2 windows

    Scenario: The topic page summary is the same sentence as its findings card
      Given a topic with a confirmed episode and framing differences
      When the whole site is built
      Then the summary on the topic page equals the headline on its findings list card

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: Counts and percentages shown match the underlying data

    Scenario Outline: Numbers on the page are formatted from the recorded values
      Given a recorded <field> of <value>
      When the topic page is generated
      Then the page shows "<shown>"

      Examples:
        | field                          | value   | shown                               |
        | durable_spine_drop             | 0.8766  | 87.7% durable-spine drop            |
        | peak_pct                       | 60.96   | 61% persistence-weighted loss       |
        | pwr_mass                       | 311631  | 311,631                             |
        | duration_seconds               | 2657    | 44 minutes                          |
        | interval pwr_removed           | 117205  | 117,205 removed                     |

    Scenario: The strongest episode is chosen by persistence-weighted mass
      Given 3 confirmed episodes with durable-spine drops 30%, 80% and 55% and masses 900, 100 and 5000
      When the topic page is generated
      Then the Overview reports the largest by mass with a 55.0% durable-spine drop

  # ------------------------------------------------------------------------------------------------
  @implemented @policy
  Rule: A topic page contains no dead links

    Scenario: Every internal link and anchor on the built topic and redline pages resolves
      Given a topic with confirmed episodes, redlines, vocabulary, citations, facts, framing and versions
      When the whole site is built
      Then every relative link on the topic page and its redline pages targets a file written by the build
      And every fragment targets an element id in that file or a tab panel on the page
      And glossary links target defined glossary terms

    Scenario: Revision links use the recorded revision ids and language hosts
      Given confirmed episodes, framing snapshots and version receipts with recorded revision ids
      When the topic page is generated
      Then every oldid link names a revision id found in the data
      And its host is the language edition recorded for that revision

    Scenario: The topic is reachable from the findings list
      Given a published topic
      When the whole site is built
      Then the findings list links to the topic page file written by the build
      And the topic page links back to the findings list

    Scenario: Withheld topics do not leave redline links behind
      Given a withheld confirmation and redline pivots left on disk
      When the whole site is built
      Then the topic page shows no link to a redline page

    @bug
    Scenario: Withheld topics do not publish orphan redline pages
      Given a withheld confirmation and redline pivots left on disk
      When the whole site is built
      Then no redline page is written for the topic
