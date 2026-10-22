//! snapshot-tokens — emit true historical token sets for chosen revisions of one page.
//!
//! Why this exists: the hosted `wikiwho-cli` emits `all_tokens` = tokens surviving in the CURRENT
//! revision (with in/out lifecycle). Reconstructing a historical snapshot from that would omit tokens
//! deleted-and-never-restored — exactly the persistent removal the drift metric measures. The wikiwho
//! *library* retains every revision's token structure, so here we walk each target revision directly.
//!
//! Usage:
//!   snapshot-tokens <page.xml> [revid1,revid2,...]
//!   (no rev list, or "all" -> every non-spam revision)
//!
//! Memory notes (Actions / ~7GB runners):
//!   - After each revision is analysed, drop its original dump text (same trick as wikiwho-cli).
//!   - When a concrete target list is given, stop after the newest target — later revisions are not
//!     loaded into the authorship graph.
//!   - An empty CSV is a no-op (exit 0), never "all" — callers that join an empty pick list must not
//!     accidentally analyse every revision.
//!
//! Output (stdout), one line per emitted revision, tab-separated:
//!   <rev_id>\t<uid>:<origin_rev_id>\t<uid>:<origin_rev_id>...
//! where uid = a stable per-page token id (wikiwho unique_id) and origin_rev_id = the revision that
//! first introduced the token. Consumed by ingest.py -> rsnap(token_id=uid, o_rev_id=origin).

use std::borrow::Borrow;
use std::collections::HashSet;
use std::env;
use std::fs::File;
use std::io::{BufReader, BufWriter, Write};
use std::rc::Rc;

use wikiwho::algorithm::PageAnalysis;
use wikiwho::dump_parser::{DumpParser, Revision, Text};
use wikiwho::utils::iterate_revision_tokens;

/// Borrow wrapper that lets `analyse_page` see a revision while we retain a mutable handle to clear
/// its text after the library has copied what it needs (wikiwho-cli `text_deleting_iterator`).
struct RevisionIterHelper<'a>(
    #[allow(clippy::redundant_allocation)] Rc<&'a mut Revision>,
);

impl<'a> Borrow<Revision> for RevisionIterHelper<'a> {
    fn borrow(&self) -> &Revision {
        &self.0
    }
}

fn text_deleting_iterator(
    revisions: &mut [Revision],
) -> impl Iterator<Item = RevisionIterHelper<'_>> {
    revisions.iter_mut().scan(None, |state, rev| {
        let this_rev = Rc::new(rev);
        let last_rev = state.replace(this_rev.clone());
        if let Some(last_rev) = last_rev {
            if let Some(last_rev) = Rc::into_inner(last_rev) {
                // Original dump text is not needed after analyse_page copies a lowercased Arc.
                last_rev.text = Text::Deleted;
            }
        }
        Some(RevisionIterHelper(this_rev))
    })
}

fn parse_targets(arg: Option<&str>) -> Result<Option<HashSet<i32>>, String> {
    match arg {
        None | Some("all") => Ok(None),
        Some("") => {
            // Explicit empty CSV from a caller with zero picks — do not treat as "all".
            Ok(Some(HashSet::new()))
        }
        Some(list) => {
            let mut set = HashSet::new();
            for part in list.split(',') {
                let part = part.trim();
                if part.is_empty() {
                    continue;
                }
                let id: i32 = part
                    .parse()
                    .map_err(|_| format!("invalid revision id in target list: {part:?}"))?;
                set.insert(id);
            }
            Ok(Some(set))
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: snapshot-tokens <page.xml> [revid1,revid2,... | all]");
        std::process::exit(2);
    }
    let xml_path = &args[1];
    let targets = parse_targets(args.get(2).map(String::as_str))?;

    if let Some(t) = &targets {
        if t.is_empty() {
            eprintln!("snapshot-tokens: empty target list — nothing to emit");
            return Ok(());
        }
    }

    let max_target = targets.as_ref().and_then(|t| t.iter().copied().max());

    let reader = BufReader::new(File::open(xml_path)?);
    let mut parser = DumpParser::new(reader)?;
    let stdout = std::io::stdout();
    let mut w = BufWriter::new(stdout.lock());

    let mut pages = 0usize;
    while let Some(mut page) = parser.parse_page()? {
        // Drop revisions newer than the newest requested snap — they only inflate RAM.
        if let Some(max_id) = max_target {
            let before = page.revisions.len();
            page.revisions.retain(|r| r.id <= max_id);
            let dropped = before - page.revisions.len();
            if dropped > 0 {
                eprintln!(
                    "snapshot-tokens: truncated {} revision(s) after max target {max_id}",
                    dropped
                );
            }
        }

        let analysis =
            match PageAnalysis::analyse_page(text_deleting_iterator(&mut page.revisions)) {
                Ok(a) => a,
                Err(e) => {
                    eprintln!("skip page {:?}: {e}", page.title);
                    continue;
                }
            };
        // Raw dump texts are Deleted; free the Vec shell before emitting long token lines.
        page.revisions.clear();
        page.revisions.shrink_to_fit();

        pages += 1;
        // Emit in chronological order for deterministic output.
        for rev_ptr in &analysis.ordered_revisions {
            let rev_id: i32 = rev_ptr.id;
            if let Some(t) = &targets {
                if !t.contains(&rev_id) {
                    continue;
                }
            }
            write!(w, "{rev_id}")?;
            for word_ptr in iterate_revision_tokens(&analysis, rev_ptr) {
                let uid = word_ptr.unique_id();
                let origin = analysis[word_ptr].origin_revision.id;
                write!(w, "\t{uid}:{origin}")?;
            }
            writeln!(w)?;
            w.flush()?;
        }
    }
    w.flush()?;
    eprintln!("snapshot-tokens: analysed {pages} page(s)");
    Ok(())
}
