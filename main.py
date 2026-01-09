import requests
import csv
import time
import sys
from config import config

# Parametry SELECT
SELECT_PARAMS = ["*", "UF_*", "PHONE", "EMAIL", "WEB"]


# --- Funkcje pomocnicze ---

def flatten_multifield(field_data):
    """
    Zamienia skomplikowaną tablicę Bitrixa na prosty string.
    """
    if isinstance(field_data, list):
        values = [item.get('VALUE', '') for item in field_data if 'VALUE' in item]
        return "; ".join(values)
    return ""


def bitrix_call(webhook_url, method, params=None):
    """
    Wysyła zapytanie do Bitrix24 z mechanizmem Retry (ponawiania prób).
    W przypadku błędu czeka 4 sekundy i próbuje ponownie.
    """
    if params is None:
        params = {}

    url = f"{webhook_url}{method}"
    max_retries = 5  # Ile razy próbować zanim się poddamy

    for attempt in range(1, max_retries + 1):
        try:
            # timeout=30 oznacza, że jeśli serwer nie odpowie w 30s, rzuci wyjątek
            response = requests.post(url, json=params, timeout=30)

            # Sprawdza czy status HTTP to 200 (jeśli 4xx lub 5xx -> rzuca błąd)
            response.raise_for_status()

            result = response.json()

            # Opcjonalnie: Czasami Bitrix zwraca 200 OK, ale w środku jsona jest 'error': 'QUERY_LIMIT_EXCEEDED'
            if "error" in result and result.get("error") == "QUERY_LIMIT_EXCEEDED":
                raise requests.exceptions.RequestException("Przekroczono limit zapytań (QUERY_LIMIT_EXCEEDED)")

            return result

        except requests.exceptions.RequestException as e:
            print(f"⚠️ Błąd połączenia/TimeOut (Próba {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                print("⏳ Czekam 4 sekundy przed ponowną próbą...")
                time.sleep(4)
            else:
                print("❌ Błąd krytyczny: Nie udało się połączyć po wszystkich próbach.")
                return {"error": str(e)}


# --- KROK 1: Pobieranie Firm (Logika stronicowania) ---

def fetch_all_companies_optimized():
    print(">>> Rozpoczynam pobieranie firm...")

    all_companies = []
    start = 0
    total_fetched_count = 0

    while True:
        params = {
            "order": {"ID": "ASC"},
            "select": SELECT_PARAMS,
            "start": start
        }

        # Tutaj wywołujemy naszą bezpieczną funkcję z retry
        r = bitrix_call(config.WEBHOOK_URL, "crm.company.list.json", params)

        if "error" in r:
            print(f"Przerwano pobieranie z powodu błędu: {r}")
            break

        batch = r.get("result", [])
        if not batch:
            break

        batch_count = len(batch)
        for company in batch:
            company["PHONE"] = flatten_multifield(company.get("PHONE"))
            company["EMAIL"] = flatten_multifield(company.get("EMAIL"))
            company["WEB"] = flatten_multifield(company.get("WEB"))
            all_companies.append(company)

        total_fetched_count += batch_count
        print(f"Pobrano kolejną partię ({batch_count} sztuk). Łącznie pobrano: {total_fetched_count} firm.")

        if "next" in r:
            start = r["next"]
        else:
            print("--- Koniec pobierania danych (brak parametru 'next') ---")
            break

    return all_companies


# --- KROK 2: Pobieranie Użytkowników i aktualizacja CSV ---

def fetch_all_users():
    print(">>> Pobieranie listy użytkowników...")
    users = []
    start = 0

    while True:
        url_base = getattr(config, 'WEBHOOK_URL_USER_GET', config.WEBHOOK_URL)

        # Również tutaj zadziała mechanizm retry
        r = bitrix_call(url_base, "user.get.json", {"start": start})

        if "error" in r:
            print("Błąd pobierania userów:", r)
            break

        batch = r.get("result", [])
        if not batch:
            break

        users.extend(batch)

        if "next" not in r:
            break
        start = r["next"]

    return users


def process_companies_with_users(raw_companies, final_output_file):
    users = fetch_all_users()

    user_map = {}
    for u in users:
        full_name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
        if not full_name:
            full_name = u.get('EMAIL', u.get('LOGIN', 'Nieznany'))
        user_map[str(u['ID'])] = full_name

    print(f"Zmapowano {len(user_map)} użytkowników.")

    print(">>> Podmieniam ID (Created/Assigned) na nazwiska...")
    for row in raw_companies:
        c_id = row.get("CREATED_BY_ID")
        if c_id and str(c_id) in user_map:
            row["CREATED_BY_ID"] = user_map[str(c_id)]

        a_id = row.get("ASSIGNED_BY_ID")
        if a_id and str(a_id) in user_map:
            row["ASSIGNED_BY_ID"] = user_map[str(a_id)]

    save_to_csv(final_output_file, raw_companies)


def save_to_csv(filename, rows):
    if not rows:
        print("Brak danych do zapisu.")
        return

    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())

    fields = sorted(list(all_keys))
    if "ID" in fields:
        fields.insert(0, fields.pop(fields.index("ID")))

    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"SUKCES: Zapisano plik: {filename}")
    except IOError as e:
        print(f"Błąd zapisu pliku: {e}")


# --- MAIN ---

def main():
    FILE_FINAL = "companies_full_export.csv"

    companies_data = fetch_all_companies_optimized()

    if companies_data:
        process_companies_with_users(companies_data, FILE_FINAL)
    else:
        print("Nie udało się pobrać żadnych firm.")


if __name__ == "__main__":
    main()