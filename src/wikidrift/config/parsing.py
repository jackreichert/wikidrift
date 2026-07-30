"""Pure text/URL parsing helpers (no I/O) shared across modules."""
import re

# An unregistered editor = an IPv4/IPv6 username; anon reverts skew disproportionately to vandalism.
# One definition, shared by mscore (registered-only filter) and l4 (seed-editor filter).
ANON_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9A-Fa-f:]+:[0-9A-Fa-f:]+$")


def slugify(title):
    """Article title → safe flat filename stem. Spaces → underscores (the site convention) and path
    separators collapsed, so a Wikipedia subpage title ('A/B') can neither escape the target dir nor
    nest into one (CWE-22). Unicode letters (e.g. the en-dash in 'Israeli–Palestinian conflict') are
    preserved so existing findings filenames still resolve."""
    slug = title.replace(" ", "_").replace("/", "_").replace("\\", "_").replace("\x00", "_")
    return "_" * len(slug) if slug and set(slug) == {"."} else slug


# Wayback wraps a real citation as web.archive.org/web/<ts>/<real-url>; unwrap it so an archived NYT
# cite counts as nytimes.com, not "web.archive.org" (else archiving churn swamps the change ranking).
_WAYBACK_RE = re.compile(r"https?://web\.archive\.org/web/[^/]*?/(https?://.+)$", re.I)


def citation_domains(raw, unwrap_wayback=False):
    """{domain: count} of the external-link citation domains in wikitext ('www.' stripped). Shared by
    l5_factcheck (Jaccard overlap) and l5_sources (composition). unwrap_wayback=True resolves Wayback
    wrappers to the real source and drops bare/unparseable web.archive.org links."""
    import mwparserfromhell
    import urllib.parse
    doms = {}
    for link in mwparserfromhell.parse(raw).filter_external_links():
        url = str(link.url)
        if unwrap_wayback:
            m = _WAYBACK_RE.search(url)
            if m:
                url = m.group(1)
        net = urllib.parse.urlparse(url).netloc.lower()
        net = net[4:] if net.startswith("www.") else net
        if not net or (unwrap_wayback and net == "web.archive.org"):
            continue
        doms[net] = doms.get(net, 0) + 1
    return doms
