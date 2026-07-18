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
//! Output (stdout), one line per emitted revision, tab-separated:
//!   <rev_id>\t<uid>:<origin_rev_id>\t<uid>:<origin_rev_id>...
//! where uid = a stable per-page token id (wikiwho unique_id) and origin_rev_id = the revision that
//! first introduced the token. Consumed by ingest_local.py -> rsnap(token_id=uid, o_rev_id=origin).

use std::collections::HashSet;
use std::env;
use std::fs::File;
use std::io::{BufReader, BufWriter, Write};

use wikiwho::algorithm::PageAnalysis;
use wikiwho::dump_parser::DumpParser;
use wikiwho::utils::iterate_revision_tokens;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: snapshot-tokens <page.xml> [revid1,revid2,... | all]");
        std::process::exit(2);
    }
    let xml_path = &args[1];
    let targets: Option<HashSet<i32>> = match args.get(2).map(String::as_str) {
        None | Some("") | Some("all") => None,
        Some(list) => Some(list.split(',').filter_map(|x| x.trim().parse::<i32>().ok()).collect()),
    };

    let reader = BufReader::new(File::open(xml_path)?);
    let mut parser = DumpParser::new(reader)?;
    let stdout = std::io::stdout();
    let mut w = BufWriter::new(stdout.lock());

    let mut pages = 0usize;
    while let Some(page) = parser.parse_page()? {
        let analysis = match PageAnalysis::analyse_page(&page.revisions) {
            Ok(a) => a,
            Err(e) => {
                eprintln!("skip page {:?}: {e}", page.title);
                continue;
            }
        };
        pages += 1;
        // Emit in chronological order for deterministic output.
        for rev_ptr in &analysis.ordered_revisions {
            let rev_id: i32 = rev_ptr.id;
            if let Some(t) = &targets {
                if !t.contains(&rev_id) {
                    continue;
                }
            }
            let mut line = rev_id.to_string();
            for word_ptr in iterate_revision_tokens(&analysis, rev_ptr) {
                let uid = word_ptr.unique_id();
                let origin = analysis[word_ptr].origin_revision.id;
                line.push('\t');
                line.push_str(&uid.to_string());
                line.push(':');
                line.push_str(&origin.to_string());
            }
            writeln!(w, "{line}")?;
        }
    }
    w.flush()?;
    eprintln!("snapshot-tokens: analysed {pages} page(s)");
    Ok(())
}
