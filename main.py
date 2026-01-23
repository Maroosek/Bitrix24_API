import requests
import csv
import time
from config import BitrixConfig

#TODO tidy this up for actual use in API

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


def fetch_companies_by_industry(industry: str):
    """
    Pobiera wszystkie firmy, filtruje je po polu INDUSTRY.
    Dodatkowo sprawdza, czy firma posiada dane adresowe (Ulica, Miasto lub Kod).
    Czyści adres z 'ul.', kropek i przecinków przed zwróceniem.
    Zwraca tylko firmy posiadające minimum jeden z tych parametrów.
    """
    print(f"🔍 Rozpoczynam poszukiwanie firm z branży: '{industry}'")

    # Pobieramy firmy (zachowałem Twój start=5300)
    all_companies = fetch_all_companies_optimized(start=5300)

    valid_companies = []
    missing_address_count = 0

    print(">>> Rozpoczynam filtrowanie i czyszczenie danych...")

    for company in all_companies:
        # 1. Sprawdzenie branży
        comp_industry = company.get("INDUSTRY")

        if str(comp_industry) != industry:
            continue

        # 2. Pobieranie danych adresowych
        city = str(company.get("UF_CRM_CITY", "") or "").strip()
        postal_code = str(company.get("UF_CRM_POST_CODE", "") or "").strip()
        raw_address = str(company.get("UF_CRM_68C9578E889C6", "") or "").strip()

        # 3. CZYSZCZENIE ADRESU (Nowa sekcja)
        clean_address = raw_address

        # Usuwanie wariantów "ul." / "ul " (wielkość liter uwzględniona w liście)
        # Robimy to przed usunięciem kropek, aby wyłapać "ul." w całości
        prefixes_to_remove = ["ul.", "Ul.", "UL.", "ul ", "Ul ", "UL "]
        for prefix in prefixes_to_remove:
            clean_address = clean_address.replace(prefix, "")

        # Usuwanie kropek i przecinków oraz zbędnych spacji
        clean_address = clean_address.replace(".", "").replace(",", "").strip()

        # WAŻNE: Nadpisujemy surowy adres wersją wyczyszczoną w obiekcie firmy
        # Dzięki temu zwracana lista i printy będą miały czysty format
        company["UF_CRM_68C9578E889C6"] = clean_address

        # 4. Warunek: Musi istnieć Miasto LUB Kod LUB Ulica (po oczyszczeniu)
        # Sprawdzamy na clean_address, który jest już pozbawiony "ul." itp.
        has_address_data = (len(city) > 0 and len(clean_address) > 0) or (
                    len(postal_code) > 0 and len(clean_address) > 0)

        if has_address_data:
            valid_companies.append(company)
        else:
            missing_address_count += 1

    # 5. Printowanie wyników
    print(f"\n--- WYNIKI FILTROWANIA (Branża: {industry}) ---")

    if valid_companies:
        for idx, comp in enumerate(valid_companies, 1):
            c_id = comp.get("ID", "N/A")
            c_title = comp.get("TITLE", "Bez nazwy")
            c_phone = comp.get("PHONE", "")
            c_email = comp.get("EMAIL", "")
            c_website = comp.get("WEB", "")

            c_postal_code = comp.get("UF_CRM_POST_CODE", "")
            c_assigned_by = comp.get("ASSIGNED_BY_ID", "")
            c_nip = comp.get("UF_CRM_78FF9738", "")
            c_city = comp.get("UF_CRM_CITY", "")

            # Tutaj pobieramy już wyczyszczony adres
            c_address = comp.get("UF_CRM_68C9578E889C6", "")

            print(f"{idx}. [ID: {c_id} {c_title}] Telefon: {c_phone}, E-mail: {c_email}, WWW: {c_website},"
                  f" Kod: {c_postal_code}, ID dodającego: {c_assigned_by}, NIP: {c_nip}, Miasto: {c_city}, Adres: {c_address} ")
    else:
        print("Brak firm spełniających kryteria (Branża + Adres).")

    print(f"-----------------------------------------------")
    print(f"✅ Znaleziono poprawnych (z adresem): {len(valid_companies)}")
    print(f"❌ Odrzucono (brak adresu, ale dobra branża): {missing_address_count}")
    print(f"-----------------------------------------------\n")

    return valid_companies


def fetch_all_companies_optimized(start: int):
    print(">>> Rozpoczynam pobieranie firm...")

    all_companies = []
    #start = 7300
    total_fetched_count = 0

    while True:
        params = {
            "order": {"ID": "ASC"},
            "select": SELECT_PARAMS,
            "start": start
        }

        # Tutaj wywołujemy naszą bezpieczną funkcję z retry
        r = bitrix_call(BitrixConfig.WEBHOOK_URL, "crm.company.list.json", params)

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
        url_base = getattr(BitrixConfig, 'WEBHOOK_URL_USER_GET', BitrixConfig.WEBHOOK_URL)

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


# --- Pobieranie dostępnych pól i aktualizacja CSV ---

def fetch_all_fields():
    print(">>> Pobieranie listy dostępnych pól...")
    fields = []
    start = 0

    while True:
        url_base = getattr(BitrixConfig, 'WEBHOOK_URL_FIELDS_GET', BitrixConfig.WEBHOOK_URL_FIELDS_GET)

        # Również tutaj zadziała mechanizm retry
        r = bitrix_call(url_base, "crm.status.list.json", {"start": start})

        if "error" in r:
            print("Błąd pobierania userów:", r)
            break

        batch = r.get("result", [])
        if not batch:
            break

        fields.extend(batch)

        if "next" not in r:
            break
        start = r["next"]

    return fields


# Not working properly
def fetch_all_fields_contacts():
    print(">>> Pobieranie listy dostępnych pól w kontaktach...")
    fields = []
    start = 0

    while True:
        url_base = getattr(BitrixConfig, 'WEBHOOK_URL_FIELDS_CONTACT_GET',
                           BitrixConfig.WEBHOOK_URL_FIELDS_CONTACT_GET)

        # Również tutaj zadziała mechanizm retry
        r = bitrix_call(url_base, "crm.contact.userfield.list.json", {"start": start})

        if "error" in r:
            print("Błąd pobierania userów:", r)
            break

        batch = r.get("result", [])
        if not batch:
            break

        fields.extend(batch)

        if "next" not in r:
            break
        start = r["next"]

    return fields


def fetch_all_companies_contacts():
    print(">>> Pobieranie listy dostępnych pól...")
    contacts = []
    start = 0
    total_fetched_count = 0

    while True:
        url_base = getattr(BitrixConfig, 'WEBHOOK_URL_COMPANY_CONTACTS_GET',
                           BitrixConfig.WEBHOOK_URL_COMPANY_CONTACTS_GET)

        # Również tutaj zadziała mechanizm retry
        r = bitrix_call(url_base,
                        "crm.contact.list.json?select[]=ID&select[]=NAME&select[]=LAST_NAME&select[]=COMPANY_ID&select[]=TYPE_ID&select[]=SOURCE_ID&select[]=ASSIGNED_BY_ID&select[]=COMMENTS&select[]=PHONE&select[]=EMAIL",
                        {"start": start})

        if "error" in r:
            print("Błąd pobierania userów:", r)
            break

        batch = r.get("result", [])
        if not batch:
            break

        contacts.extend(batch)

        batch_count = len(batch)
        total_fetched_count += batch_count
        print(f"Pobrano kolejną partię ({batch_count} sztuk). Łącznie pobrano: {total_fetched_count} kontaktów.")

        if "next" not in r:
            break
        start = r["next"]

    return contacts


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


def add_new_contact(
        name,  # Imię (Wymagane)
        last_name,  # Nazwisko (Wymagane)
        company_id=None,  # ID firmy, do której przypisać kontakt
        company_type=None,  # Typ firmy np szambo
        phone=None,  # Numer telefonu
        email=None,  # Adres e-mail
        assigned_by_id=None,  # ID osoby odpowiedzialnej (np. 183)
        type_id=None,  # Typ kontaktu (np. CLIENT, SUPPLIER)
        source_id=None,  # Źródło (np. CALL, EMAIL, WEB)
        comments=None  # Komentarze/Uwagi
):
    """
    Tworzy nowy kontakt w Bitrix24 i opcjonalnie przypisuje go do firmy.
    """
    print(f"🚀 Wysyłam dane dla kontaktu: {name} {last_name}...")

    # Budowanie struktury 'fields'
    fields = {
        "NAME": name,
        "LAST_NAME": last_name,
        "OPENED": "Y"  # Kontakt widoczny dla wszystkich
    }

    # Powiązanie z firmą (musi to być ID numeryczne firmy)
    if company_id:
        fields["COMPANY_ID"] = company_id

    if company_type:
        fields["UF_CRM_1762172335764"] = company_type

    # Osoba odpowiedzialna
    if assigned_by_id:
        fields["ASSIGNED_BY_ID"] = assigned_by_id

    # Typ kontaktu (np. CLIENT, SUPPLIER, PARTNER)
    if type_id:
        fields["TYPE_ID"] = type_id

    # Źródło (np. CALL, EMAIL, WEB, RECOMMENDATION)
    if source_id:
        fields["SOURCE_ID"] = source_id

    # Komentarz
    if comments:
        fields["COMMENTS"] = comments

    # Pola wielokrotne (Telefon) - struktura listy słowników
    if phone:
        fields["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]

    # Pola wielokrotne (Email)
    if email:
        fields["EMAIL"] = [{"VALUE": email, "VALUE_TYPE": "WORK"}]

    # Wywołanie API
    # Używamy metody crm.contact.add.json
    print("Dane kontaktu: ", fields)
    result = bitrix_call(BitrixConfig.WEBHOOK_URL_CONTACT_ADD, "crm.contact.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Sukces! Dodano nowy kontakt. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania kontaktu: {result}")
        return None


def add_new_company(
        title,  # Nazwa firmy (Wymagane)
        nip=None,  # Twój custom field: UF_CRM_78FF9738
        phone=None,  # Telefon
        email=None,  # E-mail
        website=None,  # WWW
        industry=None,  # Branża (kod, np. IT, MANUFACTURING)
        company_type=None,  # Typ firmy (kod, np. CUSTOMER, PARTNER)
        city=None,
        # province=None,
        postal_code=None,
        address=None,
        assigned_by_id=None,  # ID osoby odpowiedzialnej (np. 183)
        comments=None  # Komentarze
):
    """
    Tworzy nową firmę w Bitrix24.
    """
    print(f"🚀 Wysyłam dane dla firmy: {title}...")

    # Budowanie struktury 'fields'
    fields = {
        "TITLE": title + " - " + nip,
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

    if city:
        fields["UF_CRM_CITY"] = city

    if postal_code:
        fields["UF_CRM_POST_CODE"] = postal_code

    if address:
        fields["UF_CRM_68C9578E889C6"] = address

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
        fields["UF_CRM_9086D325"] = email

    if website:
        fields["UF_CRM_1FB9DDC7"] = website

    # Wywołanie API
    # Używamy metody crm.company.add
    print("Dodano: ", fields)
    result = bitrix_call(BitrixConfig.WEBHOOK_URL, "crm.company.add.json", {"fields": fields})

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


# --- NOWA FUNKCJA: Aktualizacja Nazw o NIP ---

def update_companies_titles_with_nip():
    """
    Pobiera firmy, sprawdza czy mają NIP (UF_CRM_78FF9738).
    Jeśli NIP jest i nie ma go w nazwie (TITLE), aktualizuje nazwę.
    """
    print(">>> Rozpoczynam aktualizację nazw firm (dodawanie NIP do TITLE)...")

    # Definiujemy ID pola NIP (łatwiej zmieniać w jednym miejscu)
    NIP_FIELD_KEY = "UF_CRM_78FF9738"

    start = 0
    total_processed = 0
    total_updated = 0

    while True:
        # Pobieramy ID, TITLE oraz NIP
        params = {
            "order": {"ID": "ASC"},
            "select": ["ID", "TITLE", NIP_FIELD_KEY],
            "start": start
        }

        r = bitrix_call(BitrixConfig.WEBHOOK_URL, "crm.company.list.json", params)

        if "error" in r:
            print(f"❌ Przerwano pobieranie z powodu błędu: {r}")
            break

        batch = r.get("result", [])
        if not batch:
            break

        for company in batch:
            # Pobieramy zmienną lokalną (cały rekord w batchu)
            c_id = company.get("ID")
            title = company.get("TITLE", "")
            nip = company.get(NIP_FIELD_KEY)

            # 1. Sprawdzamy czy pole NIP istnieje i nie jest puste
            if nip:
                nip_str = str(nip).strip()
                title_str = str(title).strip()

                # 2. Sprawdzamy czy TITLE zawiera w sobie NIP
                if nip_str in title_str:
                    # Ignorujemy
                    pass
                else:
                    # 3. Jeśli nie, aktualizujemy nazwę
                    new_title = f"{title_str} | {nip_str}"

                    update_fields = {"TITLE": new_title}

                    print(f"🔄 Aktualizacja ID {c_id}: '{title_str}' -> '{new_title}'")

                    # API call do aktualizacji
                    update_res = bitrix_call(BitrixConfig.WEBHOOK_URL_COMPANY_UPDATE, "crm.company.update.json", {
                        "id": c_id,
                        "fields": update_fields
                    })

                    if "error" in update_res:
                        print(f"   ⚠️ Błąd aktualizacji ID {c_id}: {update_res}")
                    else:
                        total_updated += 1

        batch_count = len(batch)
        total_processed += batch_count
        print(f"Przetworzono partię: {batch_count}. Łącznie: {total_processed}. Zaktualizowano nazw: {total_updated}")

        if "next" in r:
            start = r["next"]
        else:
            print("--- Koniec przetwarzania ---")
            break

    print(f"\n✅ Zakończono! Zaktualizowano {total_updated} firm.")

# --- MAIN ---

def main():
    print("--- BITRIX INTEGRATION ---")
    print("1. Pobierz wszystkie firmy do CSV (Export)")
    print("2. Pobierz wszystkie pola do CSV (Export)")
    # print("3. Pobierz wszystkie pola kontaktów do CSV (Export)")
    print("4. Pobierz wszystkie kontakty do CSV (Export)")
    print("5. Dodaj nową firmę (Import)")
    print("6. Dodaj nowy kontakt (Import)")
    print("7. Zaktualizuj nazwy firm (Dodaj NIP do nazwy, jeśli go brak)")
    print("8. Pobierz firmy po industry")

    choice = input("Wybierz opcję (1/7): ").strip()

    if choice == "1":
        FILE_FINAL = "companies_full_export.csv"
        companies_data = fetch_all_companies_optimized()
        if companies_data:
            process_companies_with_users(companies_data, FILE_FINAL)


    elif choice == "2":

        File_fields = BitrixConfig.OUTPUT_FIELDS
        fields_data = fetch_all_fields()
        if fields_data:
            save_to_csv(File_fields, fields_data)

    # elif choice == "3":
    #
    #     file_contact_fields = config.OUTPUT_CONTACT_FIELDS
    #     fields_contact_data = fetch_all_fields_contacts()
    #     if fields_contact_data:
    #         save_to_csv(file_contact_fields, fields_contact_data)

    elif choice == "4":

        File_contacts = BitrixConfig.OUTPUT_CONTACTS
        contacts_data = fetch_all_companies_contacts()
        if contacts_data:
            save_to_csv(File_contacts, contacts_data)


    elif choice == "5":

        add_new_company(
            title="Marko królestwo diggerów -TEST-",
            nip="1234567891",  # Twoje pole UF_CRM_...
            phone="600 120 210",
            email="kontakt@firma-testowa-koparki.pl",  # nie pojawia się
            website="https://firma-testowa-koparki.pl",  # nie pojawia się
            industry="IT",  # Przykładowy kod branży
            city="Kędzierzyn-Koźle",
            # province="Województwo Opolskie",
            postal_code="47-220",
            address="Miła 12",  # Pole adresu które później łączy się w pełny hash
            company_type="COMPETITOR",  # Przykładowy typ (Klient)
            assigned_by_id=1,  # ID Opiekuna (musi być liczbą/ID usera) #brak weryfikacji
            comments="Firma dodana przez skrypt Python. Giga main "
        )

    elif choice == "6":

        add_new_contact(
            name="Marko",
            last_name="KOPACZ",
            company_id=14943,
            company_type="783",
            type_id="SUPPLIER",
            phone="696969123",
            email="Marko.KOPACZ@wp.pl",
            assigned_by_id=1,
            source_id="CALL",
            comments="Klient dzwonił w sprawie pumy. -SKRYPT PYTHON-"
        )

    elif choice == "7":
        # Uruchomienie nowej logiki
        update_companies_titles_with_nip()

    elif choice == "8":
        fetch_companies_by_industry("NOTPROFIT")

    else:
        print("Nieprawidłowy wybór.")


if __name__ == "__main__":
    main()