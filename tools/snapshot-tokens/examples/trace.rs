//! Localize the markup content-drop bug: run wikiwho's naive split pipeline on the minimal
//! `<ref>` repro and print paragraph -> sentence -> token breakdown, so we can see which layer
//! loses the plain prose ("Alpha paragraph...").
use wikiwho::utils::{split_into_paragraphs_naive, split_into_sentences_naive, split_into_tokens_naive};

fn main() {
    let text = "Alpha paragraph has plain words about apples clearly stated here.\n\n\
                Beta paragraph cites something.<ref>{{cite book|title=Book|last=Smith}}</ref> More beta words follow here.\n\n\
                Gamma paragraph {{efn|a footnote with {{harvnb|Jones|2020}} inside it}} continues with prose.\n\n\
                Delta paragraph plainly mentions oranges and bananas without any markup at all here.";

    let paras = split_into_paragraphs_naive(text);
    println!("PARAGRAPHS: {}", paras.len());
    for (i, p) in paras.iter().enumerate() {
        let ptrim = p.trim();
        println!("  P{i} [{} chars]: {:?}", ptrim.len(), &ptrim[..ptrim.len().min(90)]);
        let sents = split_into_sentences_naive(ptrim);
        for (j, s) in sents.iter().enumerate() {
            let toks = split_into_tokens_naive(s);
            let toks: Vec<&str> = toks.iter().map(|c| c.as_ref()).collect();
            println!("      S{j} [{} toks]: {:?} -> {:?}", toks.len(), &s[..s.len().min(60)], toks);
        }
    }
}
