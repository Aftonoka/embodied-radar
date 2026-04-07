#!/usr/bin/env python3
"""
Verify arXiv IDs and author lists in embodied-radar/index.html
against the arXiv API. Outputs a JSON report of discrepancies.
"""

import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ARXIV_API = "http://export.arxiv.org/api/query"
BATCH_SIZE = 40
RATE_LIMIT_SECONDS = 3
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

HTML_PATH = Path(__file__).parent / "index.html"
REPORT_PATH = Path(__file__).parent / "verification_report.json"


def extract_data_entries(html: str) -> list[dict]:
    """Extract arxiv IDs and author lists from DATA array entries."""
    entries = []
    pattern = re.compile(
        r"\{id:'([^']+)'.*?name:'([^']*)'.*?arxiv:'(\d{4}\.\d{4,5})'.*?all:\[([^\]]*)\]",
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        entry_id, name, arxiv_id, authors_raw = m.groups()
        authors = [a.strip().strip("'\"") for a in authors_raw.split(",") if a.strip().strip("'\"")]
        entries.append({
            "source": "DATA",
            "entry_id": entry_id,
            "paper_name": name,
            "arxiv_id": arxiv_id,
            "file_authors": authors,
        })
    return entries


def extract_profile_entries(html: str) -> list[dict]:
    """Extract arXiv IDs from PROFILES rep_papers strings."""
    entries = []
    pattern = re.compile(
        r"rep_papers:\s*\[(.*?)\]",
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        block = m.group(1)
        arxiv_matches = re.findall(r"arXiv\s+(\d{4}\.\d{4,5})", block)
        title_matches = re.findall(r"'([^']+?)\s*\(", block)
        for i, aid in enumerate(arxiv_matches):
            title = title_matches[i] if i < len(title_matches) else ""
            ctx_start = max(0, m.start() - 200)
            ctx = html[ctx_start : m.start()]
            id_match = re.search(r"'(\w+)':\s*\{name:", ctx)
            profile_id = id_match.group(1) if id_match else "unknown"
            entries.append({
                "source": "PROFILES",
                "entry_id": profile_id,
                "paper_name": title.strip(),
                "arxiv_id": aid,
                "file_authors": [],
            })
    return entries


def extract_saved_state_entries(html: str) -> list[dict]:
    """Extract arxiv IDs from __saved_state__ JSON block."""
    entries = []
    sd_match = re.search(r'var __sd=JSON\.parse\("(.+?)"\);', html)
    if not sd_match:
        return entries
    try:
        raw = sd_match.group(1).encode().decode("unicode_escape")
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return entries

    for item in data:
        authors_info = item.get("authors", {})
        arxiv_id = authors_info.get("arxiv")
        if not arxiv_id:
            continue
        entries.append({
            "source": "SAVED_STATE",
            "entry_id": item.get("id", ""),
            "paper_name": item.get("name", ""),
            "arxiv_id": arxiv_id,
            "file_authors": authors_info.get("all", []),
        })
    return entries


def query_arxiv_batch(arxiv_ids: list[str]) -> dict[str, dict]:
    """Query arXiv API for a batch of IDs. Returns {id: {title, authors}}."""
    results = {}
    id_list = ",".join(arxiv_ids)
    url = f"{ARXIV_API}?id_list={id_list}&max_results={len(arxiv_ids)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "embodied-radar-verifier/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()
    except Exception as e:
        print(f"  API error: {e}")
        return results

    root = ET.fromstring(xml_data)
    for entry in root.findall("atom:entry", NS):
        id_elem = entry.find("atom:id", NS)
        if id_elem is None:
            continue
        full_id = id_elem.text.strip()
        arxiv_id = full_id.split("/abs/")[-1]
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id)

        title_elem = entry.find("atom:title", NS)
        title = " ".join(title_elem.text.split()) if title_elem is not None else ""

        authors = []
        for author in entry.findall("atom:author", NS):
            name_elem = author.find("atom:name", NS)
            if name_elem is not None:
                authors.append(name_elem.text.strip())

        results[arxiv_id] = {"title": title, "authors": authors}
    return results


def search_arxiv_by_title(title: str) -> str | None:
    """Search arXiv by title to find the correct ID."""
    clean = re.sub(r"[^\w\s]", " ", title).strip()
    words = clean.split()[:8]
    query = "+AND+".join(f"ti:{w}" for w in words if len(w) > 2)
    url = f"{ARXIV_API}?search_query={query}&max_results=3"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "embodied-radar-verifier/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()
    except Exception:
        return None

    root = ET.fromstring(xml_data)
    for entry in root.findall("atom:entry", NS):
        t_elem = entry.find("atom:title", NS)
        if t_elem is None:
            continue
        candidate_title = " ".join(t_elem.text.split())
        if title_similarity(title, candidate_title) > 0.7:
            id_elem = entry.find("atom:id", NS)
            if id_elem:
                aid = id_elem.text.strip().split("/abs/")[-1]
                return re.sub(r"v\d+$", "", aid)
    return None


def title_similarity(a: str, b: str) -> float:
    a_clean = re.sub(r"[^\w\s]", "", a).lower().strip()
    b_clean = re.sub(r"[^\w\s]", "", b).lower().strip()
    return SequenceMatcher(None, a_clean, b_clean).ratio()


def is_plausible_match(short_name: str, full_title: str) -> bool:
    """Check if a short project name plausibly corresponds to a full arXiv title.

    Handles cases like 'SayCan' matching 'Do As I Can, Not As I Say: Grounding...'
    or 'DreamerV3' matching 'Mastering Diverse Domains through World Models'.
    """
    sn = short_name.lower().strip()
    ft = full_title.lower().strip()

    # Direct substring: 'GR-2' in 'GR-2: A Generative Video...'
    sn_words = re.split(r"[\s/():,]+", sn)
    for w in sn_words:
        if len(w) >= 3 and w in ft:
            return True

    # Acronym-style: 'RT-1' in 'RT-1: Robotics Transformer...'
    sn_collapsed = re.sub(r"[^a-z0-9]", "", sn)
    ft_collapsed = re.sub(r"[^a-z0-9]", "", ft)
    if len(sn_collapsed) >= 3 and sn_collapsed in ft_collapsed:
        return True

    # Token overlap: significant word overlap
    stop = {"a", "an", "the", "for", "of", "and", "in", "on", "to", "via", "with", "from", "by", "at", "as"}
    sn_tokens = {w for w in re.findall(r"[a-z0-9]+", sn) if w not in stop and len(w) > 1}
    ft_tokens = {w for w in re.findall(r"[a-z0-9]+", ft) if w not in stop and len(w) > 1}
    if sn_tokens and ft_tokens:
        overlap = sn_tokens & ft_tokens
        if len(overlap) >= 1 and len(overlap) / len(sn_tokens) >= 0.3:
            return True

    return False


ROBOTICS_KEYWORDS = {
    "robot", "robotic", "manipulation", "humanoid", "locomotion", "grasping",
    "teleoperation", "imitation", "reinforcement", "diffusion", "policy",
    "navigation", "embodied", "simulation", "sim2real", "visuomotor",
    "transformer", "vla", "action", "grasp", "dexterous", "bimanual",
}


def is_related_domain(title: str) -> bool:
    """Check if a paper title suggests it's in the robotics/AI domain."""
    t = title.lower()
    return any(kw in t for kw in ROBOTICS_KEYWORDS)


def compare_authors(file_authors: list[str], api_authors: list[str]) -> dict:
    """Compare author lists and return diff info."""
    def normalize(name: str) -> str:
        return re.sub(r"\s+", " ", name).strip().lower()

    file_norm = {normalize(a) for a in file_authors if a and a != "et al."}
    api_norm = {normalize(a) for a in api_authors}

    if not file_norm:
        return {"match": True, "detail": "no_file_authors"}

    missing = api_norm - file_norm
    extra = file_norm - api_norm
    overlap = file_norm & api_norm

    if not extra and not missing:
        return {"match": True}

    return {
        "match": False,
        "overlap_count": len(overlap),
        "api_count": len(api_norm),
        "file_count": len(file_norm),
        "missing_from_file": sorted(a for a in api_authors if normalize(a) in missing),
        "fabricated_in_file": sorted(a for a in file_authors if a and a != "et al." and normalize(a) in extra),
    }


def main():
    html = HTML_PATH.read_text(encoding="utf-8")
    print(f"Read {len(html)} chars from {HTML_PATH.name}")

    data_entries = extract_data_entries(html)
    profile_entries = extract_profile_entries(html)
    saved_entries = extract_saved_state_entries(html)

    print(f"Extracted: {len(data_entries)} DATA entries, {len(profile_entries)} PROFILE entries, {len(saved_entries)} SAVED_STATE entries")

    all_by_arxiv: dict[str, list[dict]] = defaultdict(list)
    for e in data_entries + profile_entries + saved_entries:
        all_by_arxiv[e["arxiv_id"]].append(e)

    unique_ids = sorted(all_by_arxiv.keys())
    print(f"Unique arXiv IDs to verify: {len(unique_ids)}")

    api_results = {}
    for i in range(0, len(unique_ids), BATCH_SIZE):
        batch = unique_ids[i : i + BATCH_SIZE]
        print(f"  Querying batch {i // BATCH_SIZE + 1}: {len(batch)} IDs...")
        results = query_arxiv_batch(batch)
        api_results.update(results)
        if i + BATCH_SIZE < len(unique_ids):
            print(f"  Rate limit pause ({RATE_LIMIT_SECONDS}s)...")
            time.sleep(RATE_LIMIT_SECONDS)

    print(f"API returned data for {len(api_results)} / {len(unique_ids)} IDs")

    report = {}
    stats = {"total": len(unique_ids), "correct": 0, "authors_wrong": 0, "id_mismatch": 0, "not_found": 0}

    for arxiv_id in unique_ids:
        entries = all_by_arxiv[arxiv_id]
        best_entry = next((e for e in entries if e["source"] == "DATA"), entries[0])
        file_title = best_entry["paper_name"]
        file_authors = best_entry["file_authors"]

        if arxiv_id not in api_results:
            report[arxiv_id] = {
                "status": "not_found",
                "file_title": file_title,
                "sources": [e["source"] + ":" + e["entry_id"] for e in entries],
            }
            stats["not_found"] += 1
            continue

        api = api_results[arxiv_id]
        sim = title_similarity(file_title, api["title"]) if file_title else 0
        plausible = is_plausible_match(file_title, api["title"]) if file_title else False
        related = is_related_domain(api["title"])

        title_ok = sim > 0.4 or plausible

        if not title_ok and file_title:
            severity = "id_wrong_unrelated" if not related else "id_wrong_related"
            print(f"  {severity.upper()}: {arxiv_id}")
            print(f"    File:  {file_title[:80]}")
            print(f"    arXiv: {api['title'][:80]}")
            suggested = None
            if severity == "id_wrong_unrelated":
                suggested = search_arxiv_by_title(file_title)
                if suggested:
                    print(f"    Suggested: {suggested}")
                time.sleep(RATE_LIMIT_SECONDS)
            report[arxiv_id] = {
                "status": severity,
                "file_title": file_title,
                "api_title": api["title"],
                "suggested_id": suggested,
                "correct_authors_for_this_id": api["authors"],
                "sources": [e["source"] + ":" + e["entry_id"] for e in entries],
            }
            stats["id_mismatch"] += 1
        else:
            author_diff = compare_authors(file_authors, api["authors"])
            if author_diff["match"]:
                report[arxiv_id] = {
                    "status": "correct",
                    "api_title": api["title"],
                    "sources": [e["source"] + ":" + e["entry_id"] for e in entries],
                }
                stats["correct"] += 1
            else:
                report[arxiv_id] = {
                    "status": "authors_wrong",
                    "file_title": file_title,
                    "api_title": api["title"],
                    "title_similarity": round(sim, 2),
                    "correct_authors": api["authors"],
                    "file_authors": file_authors,
                    "diff": author_diff,
                    "sources": [e["source"] + ":" + e["entry_id"] for e in entries],
                }
                stats["authors_wrong"] += 1

    report["__stats__"] = stats
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport saved to {REPORT_PATH.name}")
    print(f"Stats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
