#!/usr/bin/env python3
"""
Fix arXiv IDs and author lists in embodied-radar/index.html.
Phase 1: Fix authors for entries with correct IDs (from verification_report.json)
Phase 2: Search for correct IDs using full paper titles from PROFILES rep_papers
Phase 3: Apply all fixes to HTML + clear __saved_state__
"""

import json
import re
import shutil
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ARXIV_API = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}

HTML_PATH = Path(__file__).parent / "index.html"
REPORT_PATH = Path(__file__).parent / "verification_report.json"
FIX_LOG_PATH = Path(__file__).parent / "fix_log.json"

KNOWN_CORRECT_IDS = {
    "2204.01691", "2301.04195", "2304.13705", "2311.01378",
    "2403.03181", "2408.11812", "2410.24164", "2502.19645",
    "2509.09674",
}


def search_arxiv(title: str, max_results: int = 5) -> list[dict]:
    clean = re.sub(r"[^\w\s]", " ", title).strip()
    words = [w for w in clean.split() if len(w) > 2][:10]
    query = "+AND+".join(f"ti:{w}" for w in words)
    url = f"{ARXIV_API}?search_query={query}&max_results={max_results}&sortBy=relevance"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "embodied-radar-fixer/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()
    except Exception as e:
        print(f"    Search error: {e}")
        return []

    results = []
    root = ET.fromstring(xml_data)
    for entry in root.findall("atom:entry", NS):
        id_elem = entry.find("atom:id", NS)
        t_elem = entry.find("atom:title", NS)
        if id_elem is None or t_elem is None:
            continue
        aid = id_elem.text.strip().split("/abs/")[-1]
        aid = re.sub(r"v\d+$", "", aid)
        t = " ".join(t_elem.text.split())
        authors = [a.find("atom:name", NS).text.strip() for a in entry.findall("atom:author", NS) if a.find("atom:name", NS) is not None]
        results.append({"arxiv_id": aid, "title": t, "authors": authors})
    return results


def extract_full_titles_from_profiles(html: str) -> dict[str, str]:
    """Extract full paper titles from PROFILES rep_papers, keyed by arXiv ID."""
    titles = {}
    for m in re.finditer(r"'([^']+?)\s*\([^)]*arXiv\s+(\d{4}\.\d{4,5})[^)]*\)'", html):
        title, arxiv_id = m.groups()
        titles[arxiv_id] = title.strip()
    return titles


def extract_full_titles_from_data_notes(html: str) -> dict[str, str]:
    """Extract paper titles from DATA note fields, keyed by entry ID."""
    titles = {}
    for m in re.finditer(r"\{id:'([^']+)'.*?name:'([^']*)'.*?note:'<strong>([^<]+)</strong>", html, re.DOTALL):
        entry_id, name, note_title = m.groups()
        titles[entry_id] = note_title.strip().rstrip("。，.")
    return titles


def find_correct_id(file_title: str, full_title: str | None) -> dict | None:
    search_title = full_title if full_title and len(full_title) > 10 else file_title
    if len(search_title) < 5:
        return None

    results = search_arxiv(search_title)
    if not results:
        return None

    search_lower = search_title.lower()
    file_lower = file_title.lower()
    for r in results:
        r_lower = r["title"].lower()
        if file_lower in r_lower or any(w in r_lower for w in file_lower.split() if len(w) > 3):
            return r
        if full_title:
            ft_words = {w.lower() for w in re.findall(r"\w+", full_title) if len(w) > 3}
            rt_words = {w.lower() for w in re.findall(r"\w+", r["title"]) if len(w) > 3}
            if ft_words and rt_words and len(ft_words & rt_words) / len(ft_words) > 0.5:
                return r
    return None


def fix_authors_in_data_section(html: str, arxiv_id: str, correct_authors: list[str]) -> str:
    """Fix the authors.all array for a DATA entry matching the given arxiv_id."""
    pattern = re.compile(
        r"(arxiv:'" + re.escape(arxiv_id) + r"',\s*all:\[)([^\]]*?)(\])",
        re.DOTALL,
    )
    def replace_authors(m):
        author_str = ",".join(f"'{a}'" for a in correct_authors)
        return m.group(1) + author_str + m.group(3)

    new_html, count = pattern.subn(replace_authors, html)
    return new_html


def fix_authors_in_saved_state(html: str, arxiv_id: str, correct_authors: list[str]) -> str:
    """Fix authors in __saved_state__ JSON for matching arxiv ID."""
    sd_pattern = re.compile(r'(var __sd=JSON\.parse\(")(.+?)("\);)')

    def replace_in_sd(m):
        prefix, raw_json_escaped, suffix = m.groups()
        try:
            raw = raw_json_escaped.encode().decode("unicode_escape")
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return m.group(0)

        for item in data:
            authors_info = item.get("authors", {})
            if authors_info.get("arxiv") == arxiv_id:
                authors_info["all"] = correct_authors
                if correct_authors and authors_info.get("first") not in correct_authors:
                    authors_info["first"] = correct_authors[0]

        new_json = json.dumps(data, ensure_ascii=False)
        new_escaped = new_json.replace("\\", "\\\\").replace('"', '\\"')
        return prefix + new_escaped + suffix

    return sd_pattern.sub(replace_in_sd, html)


def fix_arxiv_id_in_data_section(html: str, old_id: str, new_id: str) -> str:
    return html.replace(f"arxiv:'{old_id}'", f"arxiv:'{new_id}'")


def fix_arxiv_id_in_profiles(html: str, old_id: str, new_id: str) -> str:
    return html.replace(f"arXiv {old_id}", f"arXiv {new_id}")


def fix_arxiv_id_in_saved_state(html: str, old_id: str, new_id: str) -> str:
    old_escaped = old_id.replace(".", "\\\\.")
    new_escaped = new_id
    return re.sub(
        rf'(\\"arxiv\\":\\")({re.escape(old_id)})(\\")',
        rf'\g<1>{new_id}\g<3>',
        html,
    )


def fix_arxiv_tag_in_html(html: str, old_id: str, new_id: str) -> str:
    return html.replace(f"arXiv:{old_id}", f"arXiv:{new_id}")


def clear_saved_state(html: str) -> str:
    """Remove the entire __saved_state__ script block."""
    pattern = re.compile(
        r'<script id="__saved_state__">.*?</script>',
        re.DOTALL,
    )
    new_html, count = pattern.subn("", html)
    if count:
        print(f"  Cleared __saved_state__ block ({count} occurrence)")
    return new_html


def main():
    if not REPORT_PATH.exists():
        print("ERROR: Run verify_arxiv.py first to generate verification_report.json")
        sys.exit(1)

    report = json.load(REPORT_PATH.open())
    stats = report.pop("__stats__", {})
    html = HTML_PATH.read_text(encoding="utf-8")

    backup_path = HTML_PATH.with_suffix(".html.bak")
    shutil.copy2(HTML_PATH, backup_path)
    print(f"Backed up to {backup_path.name}")

    profile_titles = extract_full_titles_from_profiles(html)
    note_titles = extract_full_titles_from_data_notes(html)
    print(f"Extracted {len(profile_titles)} full titles from PROFILES, {len(note_titles)} from DATA notes")

    fix_log = {"timestamp": datetime.now().isoformat(), "fixes": [], "unfixable": []}

    # Phase 1: Fix authors where arXiv ID is confirmed correct
    authors_wrong = {k: v for k, v in report.items() if v.get("status") == "authors_wrong"}
    print(f"\n=== Phase 1: Fix {len(authors_wrong)} entries with wrong authors ===")
    for arxiv_id, entry in sorted(authors_wrong.items()):
        correct = entry["correct_authors"]
        html = fix_authors_in_data_section(html, arxiv_id, correct)
        print(f"  Fixed authors for {arxiv_id} ({entry.get('file_title', '')[:30]})")
        fix_log["fixes"].append({
            "type": "authors_fix",
            "arxiv_id": arxiv_id,
            "paper": entry.get("file_title", ""),
            "old_authors": entry.get("file_authors", []),
            "new_authors": correct,
        })

    # Phase 1b: Fix known-correct IDs that were flagged as mismatches
    known_correct_flagged = {k: v for k, v in report.items()
                            if v.get("status") in ("id_wrong_related", "id_wrong_unrelated")
                            and k in KNOWN_CORRECT_IDS}
    print(f"\n=== Phase 1b: {len(known_correct_flagged)} known-correct IDs (false positives, skip) ===")
    for arxiv_id in sorted(known_correct_flagged):
        print(f"  Skipping {arxiv_id} (known correct)")

    # Phase 2: Search for correct IDs
    wrong_ids = {k: v for k, v in report.items()
                 if v.get("status") in ("id_wrong_related", "id_wrong_unrelated")
                 and k not in KNOWN_CORRECT_IDS}
    print(f"\n=== Phase 2: Search correct IDs for {len(wrong_ids)} entries ===")

    id_fixes = {}
    for arxiv_id, entry in sorted(wrong_ids.items()):
        file_title = entry.get("file_title", "")
        full_title = profile_titles.get(arxiv_id)

        sources = entry.get("sources", [])
        entry_ids = [s.split(":")[-1] for s in sources if ":" in s]
        for eid in entry_ids:
            if eid in note_titles and not full_title:
                full_title = note_titles[eid]

        search_label = full_title[:60] if full_title else file_title[:60]
        print(f"  Searching for {arxiv_id} ({file_title[:25]}) using: {search_label}...")

        result = find_correct_id(file_title, full_title)
        time.sleep(3)

        if result:
            print(f"    FOUND: {result['arxiv_id']} - {result['title'][:60]}")
            id_fixes[arxiv_id] = result
            fix_log["fixes"].append({
                "type": "id_fix",
                "old_id": arxiv_id,
                "new_id": result["arxiv_id"],
                "paper": file_title,
                "correct_title": result["title"],
                "correct_authors": result["authors"],
            })
        else:
            print(f"    NOT FOUND - will be flagged for manual review")
            fix_log["unfixable"].append({
                "arxiv_id": arxiv_id,
                "file_title": file_title,
                "full_title_searched": full_title,
            })

    # Phase 3: Apply ID fixes
    print(f"\n=== Phase 3: Apply {len(id_fixes)} ID fixes ===")
    for old_id, result in sorted(id_fixes.items()):
        new_id = result["arxiv_id"]
        html = fix_arxiv_id_in_data_section(html, old_id, new_id)
        html = fix_arxiv_id_in_profiles(html, old_id, new_id)
        html = fix_arxiv_tag_in_html(html, old_id, new_id)
        html = fix_authors_in_data_section(html, new_id, result["authors"])
        print(f"  {old_id} → {new_id}")

    # Phase 4: Clear __saved_state__
    print(f"\n=== Phase 4: Clear __saved_state__ ===")
    html = clear_saved_state(html)

    # Write fixed HTML
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"\nWrote fixed HTML to {HTML_PATH.name}")

    # Write fix log
    fix_log["summary"] = {
        "authors_fixed": len(authors_wrong),
        "ids_fixed": len(id_fixes),
        "unfixable": len(fix_log["unfixable"]),
        "known_correct_skipped": len(known_correct_flagged),
    }
    FIX_LOG_PATH.write_text(json.dumps(fix_log, indent=2, ensure_ascii=False))
    print(f"Fix log saved to {FIX_LOG_PATH.name}")
    print(f"\nSummary:")
    print(f"  Authors fixed: {len(authors_wrong)}")
    print(f"  IDs corrected: {len(id_fixes)}")
    print(f"  Unfixable (manual review): {len(fix_log['unfixable'])}")


if __name__ == "__main__":
    main()
