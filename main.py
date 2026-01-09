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


def add_new_company(
        title,  # Nazwa firmy (Wymagane)
        nip=None,  # Twój custom field: UF_CRM_78FF9738
        phone=None,  # Telefon
        email=None,  # E-mail
        website=None,  # WWW
        industry=None,  # Branża (kod, np. IT, MANUFACTURING)
        company_type=None,  # Typ firmy (kod, np. CUSTOMER, PARTNER)
        assigned_by_id=None,  # ID osoby odpowiedzialnej (np. 183)
        comments=None  # Komentarze
):
    """
    Tworzy nową firmę w Bitrix24.
    """
    print(f"🚀 Wysyłam dane dla firmy: {title}...")

    # Budowanie struktury 'fields'
    fields = {
        "TITLE": title,
        "OPENED": "Y"  # Dostępna dla wszystkich (opcjonalne)
    }

    # Pole niestandardowe NIP
    if nip:
        fields["UF_CRM_78FF9738"] = nip

    # Pola proste (Słownikowe)
    # UWAGA: Bitrix wymaga tutaj KODU (np. "IT"), a nie polskiej nazwy ("Informatyka").
    if industry:
        fields["INDUSTRY"] = industry
    if company_type:
        fields["COMPANY_TYPE"] = company_type

    # Osoba odpowiedzialna (musi to być ID numeryczne użytkownika, np. 1, 15, 183)
    if assigned_by_id:
        fields["ASSIGNED_BY_ID"] = assigned_by_id

    # Komentarz (HTML lub tekst)
    if comments:
        fields["COMMENTS"] = comments

    # Pola wielokrotne (Telefon, Email, Web) wymagają specjalnej struktury listy
    if phone:
        fields["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]

    if email:
        fields["EMAIL"] = [{"VALUE": email, "VALUE_TYPE": "WORK"}]

    if website:
        fields["WEB"] = [{"VALUE": website, "VALUE_TYPE": "WORK"}]

    # Wywołanie API
    # Używamy metody crm.company.add
    result = bitrix_call(config.WEBHOOK_URL, "crm.company.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Sukces! Dodano nową firmę. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania firmy: {result}")
        return None

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
    print("--- BITRIX INTEGRATION ---")
    print("1. Pobierz wszystkie firmy do CSV (Export)")
    print("2. Dodaj nową firmę (Import)")

    choice = input("Wybierz opcję (1/2): ").strip()

    if choice == "1":
        FILE_FINAL = "companies_full_export.csv"
        companies_data = fetch_all_companies_optimized()
        if companies_data:
            process_companies_with_users(companies_data, FILE_FINAL)

    elif choice == "2":
        # --- PRZYKŁAD DANYCH DO DODANIA ---
        # Możesz te dane pobrać np. z innego pliku CSV lub input()

        # UWAGA: Branża i Typ Firmy wymagają kodów systemowych (np. IT, MANUFACTURING), a nie polskich nazw.
        # Aby poznać swoje kody, trzeba użyć metody crm.status.list

        add_new_company(
            title="Nowa Firma Testowa S.A.",
            nip="1234567890",  # Twoje pole UF_CRM_...
            phone="600 100 200",
            email="kontakt@firma-testowa.pl",
            website="https://firma-testowa.pl",
            industry="IT",  # Przykładowy kod branży
            company_type="CUSTOMER",  # Przykładowy typ (Klient)
            assigned_by_id=1,  # ID Opiekuna (musi być liczbą/ID usera)
            comments="Firma dodana przez skrypt Python."
        )

    else:
        print("Nieprawidłowy wybór.")


if __name__ == "__main__":
    main()