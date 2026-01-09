import requests
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import config




# -- Update ID --
def replace_created_by_id(old_id, new_value):
    rows = []

    # Wczytanie pliku CSV
    with open(config.INPUT_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Sprawdzenie kolumny CREATED_BY_ID
            if row.get("CREATED_BY_ID") == str(old_id):
                row["CREATED_BY_ID"] = new_value
            rows.append(row)

    # Pobranie wszystkich nagłówków (dynamicznie)
    fieldnames = rows[0].keys() if rows else []

    # Zapisanie do nowego pliku CSV
    with open(config.OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Zmieniono ID {old_id} na '{new_value}' i zapisano do {config.OUTPUT_FILE}")

# --- Step 1: fetch basic companies ---
def fetch_basic_companies(limit, entryID):
    start = entryID # ID of first entry to start fetching
    companies = []

    while len(companies) < limit:
        r = requests.get(
            f"{config.WEBHOOK_URL}crm.company.list.json",
            params={"start": start, "select": ["ID","TITLE","HAS_PHONE","HAS_EMAIL"]}
        ).json()

        if "error" in r:
            print("Bitrix error:", r["error"], r.get("error_description"))
            continue

        batch = r.get("result", [])
        companies.extend(batch)

        if "next" not in r:
            break
        start = r["next"]

    return companies[:limit]

# --- Step 2: fetch full company data ---
def fetch_full_company(company_id):
    while True:
        r = requests.get(f"{config.WEBHOOK_URL}crm.company.get.json", params={"id": company_id}).json()
        if "error" in r:
            print(f"Retry company {company_id}: {r['error']}")
            continue
        print(f"Fetched company {company_id}")
        return r["result"]

# Flatten multi-fields
def flatten_multifield(field):
    if not field:
        return ""
    return "; ".join(f"{f.get('VALUE')} ({f.get('VALUE_TYPE')})" for f in field)

def fetch_all_users():
    users = []
    start = 0
    while True:
        r = requests.get(
            f"{config.WEBHOOK_URL_USER_GET}user.get.json",
            params={"start": start}
        ).json()

        if "error" in r:
            print("Error fetching users:", r)
            break

        batch = r.get("result", [])
        users.extend(batch)

        if "next" not in r:
            break
        start = r["next"]

    return users

# Save CSV
def save_to_csv(filename, rows):
    if not rows:
        print("No data to save")
        return
    fields = sorted({k for r in rows for k in r.keys()})
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {filename}")

# MAIN
def main():
    # Step 1: fetch basic

    #replace_created_by_id(old_id=183, new_value="Jakub Janczak")

    bitrix_users = fetch_all_users()
    save_to_csv(config.OUTPUT_USERS, bitrix_users)

    # basic_companies = fetch_basic_companies(config.MAX_RECORDS, 0)
    # save_to_csv(config.OUTPUT_BASIC, basic_companies)
    #
    # # Step 2: fetch full in parallel if HAS_PHONE or HAS_EMAIL
    # full_rows = []
    #
    # # Prepare list of IDs that need full fetch
    # ids_to_fetch = [c["ID"] for c in basic_companies if c.get("HAS_PHONE") == "Y" or c.get("HAS_EMAIL") == "Y"]
    #
    # with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
    #     future_to_id = {executor.submit(fetch_full_company, cid): cid for cid in ids_to_fetch}
    #     for future in as_completed(future_to_id):
    #         full = future.result()
    #         # Flatten fields
    #         full["PHONE"] = flatten_multifield(full.get("PHONE"))
    #         full["EMAIL"] = flatten_multifield(full.get("EMAIL"))
    #         full["WEB"] = flatten_multifield(full.get("WEB"))
    #         full_rows.append(full)
    #
    # # Add companies with no phone/email as basic info
    # no_phone_email = [c for c in basic_companies if c.get("HAS_PHONE") != "Y" and c.get("HAS_EMAIL") != "Y"]
    # full_rows.extend(no_phone_email)
    #
    # save_to_csv(config.OUTPUT_FULL, full_rows)
    # print(f"Done! Total companies processed: {len(full_rows)}")

if __name__ == "__main__":
    main()