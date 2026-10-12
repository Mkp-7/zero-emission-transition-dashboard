"""
MTA Zero-Emission Bus Transition Plan (2022) — Depot Data Extractor
--------------------------------------------------------------------
Extracts 4 clean, depot-level tables from the source PDF using text-based
regex parsing (no RAG, no vector search — this is a one-time structured
extraction job against a static document).

Tables extracted:
  1. Charger Capacity (page 21)      -> scheduled_buses, low_mw, high_mw
  2. New Power Supply (page 23)      -> existing_unused_mw, req_low_mw, req_high_mw, new_supply_low_mw, new_supply_high_mw
  3. Facility Condition (page 26)    -> general_condition, electrical, space, ceiling_height, two_or_more_constrained
  4. Environmental Justice Score (46)-> division, ej_score, borough_rank, system_rank

Output: one combined CSV, one row per depot, joined on depot name.

NOTE: Depot naming must match exactly across tables for the join to work.
A validation step below checks for mismatches before writing final CSV.
"""

import pdfplumber
import re
import csv

PDF_PATH = r"C:\Users\mkpat\OneDrive\Desktop\MTA Zero-Emission Transition Plan\MTA Zero-Emission Transition Plan.pdf"


def normalize_depot_name(name):
    """Reconcile known naming inconsistencies between tables."""
    aliases = {
        "JFK": "John F. Kennedy",
    }
    name = name.strip()
    return aliases.get(name, name)


def get_page_text(page_index_zero_based):
    with pdfplumber.open(PDF_PATH) as pdf:
        return pdf.pages[page_index_zero_based].extract_text()


# ---------------------------------------------------------------------------
# 1. Charger Capacity Table (page index 20)
# ---------------------------------------------------------------------------
def extract_charger_capacity():
    text = get_page_text(20)
    lines = text.split("\n")
    pattern = re.compile(r"^([A-Za-z][A-Za-z\. ]+?)\s+([\d,]+)\s+([\d.]+)\s+([\d.]+)$")
    rows = {}
    capture = False
    for line in lines:
        line = line.strip()
        if line.startswith("Eastchester"):
            capture = True
        if not capture:
            continue
        if line.startswith("System Total"):
            break
        m = pattern.match(line)
        if m:
            rows[normalize_depot_name(m.group(1))] = {
                "scheduled_buses": int(m.group(2).replace(",", "")),
                "charger_low_mw": float(m.group(3)),
                "charger_high_mw": float(m.group(4)),
            }
    return rows


# ---------------------------------------------------------------------------
# 2. New Power Supply Table (page index 22)
# ---------------------------------------------------------------------------
def extract_power_supply():
    text = get_page_text(22)
    lines = text.split("\n")
    # Depot  ExistingUnused  ReqLow  ReqHigh  NewSupplyLow  NewSupplyHigh
    pattern = re.compile(
        r"^([A-Za-z][A-Za-z\. ]+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$"
    )
    rows = {}
    capture = False
    for line in lines:
        line = line.strip()
        if line.startswith("Eastchester"):
            capture = True
        if not capture:
            continue
        if line.startswith("System Total"):
            break
        m = pattern.match(line)
        if m:
            rows[normalize_depot_name(m.group(1))] = {
                "existing_unused_mw": float(m.group(2)),
                "required_capacity_low_mw": float(m.group(3)),
                "required_capacity_high_mw": float(m.group(4)),
                "new_power_supply_low_mw": float(m.group(5)),
                "new_power_supply_high_mw": float(m.group(6)),
            }
    return rows


# ---------------------------------------------------------------------------
# 3. Facility Condition Table (page index 25)
# ---------------------------------------------------------------------------
def extract_facility_condition():
    text = get_page_text(25)
    lines = text.split("\n")
    # Depot name followed by 4 required rating tokens (G/M/C/NA/*), then an
    # optional 5th token "Y" (marks "2 or more constrained"). NA can appear
    # in the ceiling-height slot (many depots have no indoor parking >15%).
    pattern = re.compile(
        r"^([A-Za-z][A-Za-z\. ]+?)\s+([GMC\*]|NA)\s+([GMC\*]|NA)\s+([GMC\*]|NA)\s+([GMC\*]|NA)(?:\s+(Y))?$"
    )
    rows = {}
    capture = False
    for line in lines:
        line = line.strip()
        if line.startswith("Eastchester"):
            capture = True
        if not capture:
            continue
        if line.startswith("*East NY"):
            break
        m = pattern.match(line)
        if m:
            rows[normalize_depot_name(m.group(1))] = {
                "general_condition": m.group(2),
                "electrical": m.group(3),
                "space": m.group(4),
                "ceiling_height": m.group(5),
                "two_plus_constrained": m.group(6) or "",
            }
    return rows


# ---------------------------------------------------------------------------
# 4. Environmental Justice Score Table (page index 45)
# ---------------------------------------------------------------------------
def extract_ej_score():
    text = get_page_text(45)
    lines = text.split("\n")
    # Division names appear only once per group, prefixed to the first depot
    # in that group (e.g. "Bronx Eastchester 44 5 7"). Subsequent depots in
    # the same division appear without a division prefix. We match known
    # division names explicitly rather than guessing, to avoid incorrectly
    # splitting multi-word depot names like "Queens Village".
    divisions = ["Staten Island", "Bronx", "Brooklyn", "Manhattan", "Queens"]
    pattern = re.compile(r"^(.+?)\s+(\d+)\s+(\d+)\s+(\d+)$")
    rows = {}
    current_division = None
    capture = False
    for line in lines:
        line = line.strip()
        if line.startswith("Division"):
            capture = True
            continue
        if not capture:
            continue
        if line.startswith("System Average"):
            break
        m = pattern.match(line)
        if not m:
            continue
        prefix, ej_score, borough_rank, system_rank = m.groups()
        depot_name = prefix
        # Special case: "Queens Village" is itself a depot name that also
        # starts with the division name "Queens" — must not be split.
        if prefix == "Queens Village" or prefix.endswith("Queens Village"):
            depot_name = "Queens Village"
            # division stays as whatever it currently is (Queens)
            current_division = "Queens"
        else:
            for div in divisions:
                if prefix == div:
                    current_division = div
                    depot_name = None
                    break
                if prefix.startswith(div + " "):
                    current_division = div
                    depot_name = prefix[len(div):].strip()
                    break
        if depot_name:
            rows[normalize_depot_name(depot_name)] = {
                "division": current_division,
                "ej_score": int(ej_score),
                "ej_borough_rank": int(borough_rank),
                "ej_system_rank": int(system_rank),
            }
    return rows


# ---------------------------------------------------------------------------
# Combine + validate + write CSV
# ---------------------------------------------------------------------------
def main():
    charger = extract_charger_capacity()
    power = extract_power_supply()
    facility = extract_facility_condition()
    ej = extract_ej_score()

    print(f"Charger Capacity rows:   {len(charger)}")
    print(f"Power Supply rows:       {len(power)}")
    print(f"Facility Condition rows: {len(facility)}")
    print(f"EJ Score rows:           {len(ej)}")

    all_depots = set(charger) | set(power) | set(facility) | set(ej)
    print(f"\nTotal unique depot names across all tables: {len(all_depots)}")

    # Validation: flag any depot missing from any table (name mismatches)
    for depot in sorted(all_depots):
        missing = []
        if depot not in charger:
            missing.append("charger")
        if depot not in power:
            missing.append("power")
        if depot not in facility:
            missing.append("facility")
        if depot not in ej:
            missing.append("ej_score")
        if missing:
            print(f"  MISMATCH: '{depot}' missing from: {missing}")

    # Write combined CSV
    fieldnames = [
        "depot", "division",
        "scheduled_buses", "charger_low_mw", "charger_high_mw",
        "existing_unused_mw", "required_capacity_low_mw", "required_capacity_high_mw",
        "new_power_supply_low_mw", "new_power_supply_high_mw",
        "general_condition", "electrical", "space", "ceiling_height", "two_plus_constrained",
        "ej_score", "ej_borough_rank", "ej_system_rank",
    ]
    with open("mta_depot_electrification_data.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for depot in sorted(all_depots):
            row = {"depot": depot}
            row.update(charger.get(depot, {}))
            row.update(power.get(depot, {}))
            row.update(facility.get(depot, {}))
            row.update(ej.get(depot, {}))
            writer.writerow(row)

    print(f"\nWrote mta_depot_electrification_data.csv with {len(all_depots)} rows")


if __name__ == "__main__":
    main()
