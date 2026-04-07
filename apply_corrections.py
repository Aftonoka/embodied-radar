#!/usr/bin/env python3
"""
Apply known arXiv ID corrections to index.html.
Run AFTER fix_data.py (which already fixed authors and cleared __saved_state__).
"""

import re
import json
from pathlib import Path

HTML_PATH = Path(__file__).parent / "index.html"

# Verified corrections: old_id → new_id
ID_CORRECTIONS = {
    "2307.15818": "2312.13139",   # GR-1
    "2402.16218": "2402.16117",   # RoboCodeX
    "2403.03949": "2403.03174",   # MOKA
    "2403.09520": "2412.02699",   # UniGraspTransformer
    "2403.12481": "2409.20537",   # HPT
    "2403.12707": "2410.21229",   # HOVER
    "2409.01889": "2409.01652",   # ReKep
    "2409.16164": "2409.16283",   # Gen2Act
    "2410.01741": "2408.11805",   # ACE (teleoperation)
    "2410.02263": "2410.07186",   # ManiFoundation (approx)
    "2410.07655": "2403.17367",   # RoboDuet
    "2410.10034": "2410.10394",   # PIVOT-R
    "2410.10076": "2407.03162",   # Bunny-VisionPro
    "2410.13939": "2410.23004",   # DexGraspNet 2.0
    "2410.22289": "2408.14368",   # GR-MG
    "2501.08687": "2406.19972",   # HumanVLA
    "2406.06105": "2410.06158",   # GR-2 (PROFILES rep_papers)
}

# These IDs were flagged as mismatches but are actually correct (nickname ≠ full title)
FALSE_POSITIVES = {
    "2204.01691",  # SayCan → "Do As I Can, Not As I Say"
    "2301.04104",  # DreamerV3 → "Mastering Diverse Domains through World Models"
    "2301.04195",  # Isaac Lab → Orbit
    "2304.13705",  # ACT → "Learning Fine-Grained Bimanual Manipulation"
    "2311.01378",  # RoboFlamingo → "Vision-Language Foundation Models as Effective Robot Imitators"
    "2403.03181",  # VQ-BeT → "Behavior Generation with Latent Actions"
    "2408.11812",  # CrossFormer → "Scaling Cross-Embodied Learning"
    "2410.24164",  # π₀ → "$π_0$: A Vision-Language-Action Flow Model"
    "2502.19645",  # OpenVLA-OFT → "Fine-Tuning Vision-Language-Action Models"
    "2509.09674",  # SimpleVLA-RL
}


def main():
    html = HTML_PATH.read_text(encoding="utf-8")
    original_len = len(html)

    fixes_applied = 0
    for old_id, new_id in sorted(ID_CORRECTIONS.items()):
        occurrences = html.count(old_id)
        if occurrences > 0:
            html = html.replace(old_id, new_id)
            print(f"  {old_id} → {new_id} ({occurrences} occurrences)")
            fixes_applied += 1
        else:
            print(f"  {old_id} → not found in file (may have been fixed already)")

    HTML_PATH.write_text(html, encoding="utf-8")

    print(f"\nApplied {fixes_applied} ID corrections")
    print(f"File size: {original_len} → {len(html)} chars")
    print(f"\nFalse positives (confirmed correct, no change needed): {len(FALSE_POSITIVES)}")
    for fid in sorted(FALSE_POSITIVES):
        print(f"  {fid}")

    remaining_wrong = [
        "2310.08164 (GROOT - can't find correct ID)",
        "2403.12945 (LeRobot - paper is 2602.22818 but very new)",
        "2403.13439 (GROOT N2 - can't find correct ID)",
        "2404.09254 (VADER - different paper on arXiv)",
        "2406.18844 (OpenEQA - CVPR 2024, arXiv ID unclear)",
        "2407.00889 (Unitree H1 - can't find correct ID)",
        "2407.10792 (UniSafe - can't find correct ID)",
        "2407.20515 (GR00T NVIDIA - can't find correct ID)",
        "2410.09821 (RoboEXP - can't find correct ID)",
        "2410.10742 (PIVOT - can't find correct ID)",
        "2410.10878 (RoboGrind - can't find correct ID)",
        "2410.11671 (OpenR - can't find correct ID)",
        "2410.17008 (RoboCook - can't find correct ID)",
        "2410.22018 (RoboPEPP - can't find correct ID)",
        "2412.06173 (OpenVLA-OFT PROFILES dup)",
        "2501.03257 (OpenVLA-OFT PROFILES dup)",
        "2501.09180 (DynaMo - can't find correct ID)",
        "2501.09447 (DexH2R - can't find correct ID)",
        "2501.10432 (AnyRotate - can't find correct ID)",
        "2501.11247 (SPEAR - can't find correct ID)",
        "2503.06669 (saved_state artifact - cleared)",
    ]
    print(f"\nRemaining unfixed ({len(remaining_wrong)} entries):")
    for r in remaining_wrong:
        print(f"  {r}")


if __name__ == "__main__":
    main()
