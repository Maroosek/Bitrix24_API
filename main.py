import os
import re

import requests
import csv
import time
from datetime import datetime
from config import BitrixConfig
from config import TelephonyConfig

#TEST
import re
from pymongo import MongoClient, DESCENDING

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
    all_companies = fetch_all_companies_optimized(start=0, industry=industry, min_date_create="2025-12-01")

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


def fetch_all_companies_optimized(start: int, industry: str = None, min_date_create: str = None):
    print(f">>> Rozpoczynam pobieranie firm (start={start})...")

    # Wyświetlamy aktywne filtry w logach, jeśli zostały podane
    if industry:
        print(f"    Aktywny filtr branży (INDUSTRY): {industry}")
    if min_date_create:
        print(f"    Aktywny filtr daty (>=DATE_CREATE): {min_date_create}")

    all_companies = []
    total_fetched_count = 0

    while True:
        params = {
            "order": {"ID": "ASC"},
            "select": SELECT_PARAMS,
            "start": start
        }

        # --- DYNAMICZNE BUDOWANIE FILTRÓW ---
        api_filters = {}
        if industry:
            api_filters["INDUSTRY"] = industry
        if min_date_create:
            api_filters[">=DATE_CREATE"] = min_date_create

        # Jeśli zdefiniowano jakiekolwiek filtry, dodajemy je do zapytania
        if api_filters:
            params["filter"] = api_filters
        # ------------------------------------

        # Tutaj wywołujemy naszą bezpieczną funkcję z retry
        r = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.company.list.json", params)

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
        url_base = getattr(BitrixConfig, 'NEW_WEBHOOK_URL', BitrixConfig.NEW_WEBHOOK_URL)

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
        url_base = getattr(BitrixConfig, 'NEW_WEBHOOK_URL', BitrixConfig.NEW_WEBHOOK_URL)

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
        url_base = getattr(BitrixConfig, 'NEW_WEBHOOK_URL',
                           BitrixConfig.NEW_WEBHOOK_URL)

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
        url_base = getattr(BitrixConfig, 'NEW_WEBHOOK_URL',
                           BitrixConfig.NEW_WEBHOOK_URL)

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


def add_new_activity(
        owner_id,  # ID elementu nadrzędnego (np. Deal ID)
        owner_type_id,  # Typ elementu (1=Lead, 2=Deal, 3=Contact, 4=Company)
        responsible_id,  # ID osoby odpowiedzialnej
        description,  # Treść (może zawierać BBCode)
        subject="SMS odebrany",
        provider_id="CRM_TODO",
        provider_type_id="TODO",
        completed="N",  # Czy zadanie jest zakończone (Y/N)
        direction="1"  # 1 = Przychodzące, 2 = Wychodzące
):
    """
    Dodaje nową aktywność (np. notatkę o SMS, zadanie) do CRM.
    """
    print(f"🚀 Wysyłam nową aktywność: '{subject}' dla ID {owner_id}...")

    # Budowanie struktury 'fields'
    fields = {
        "OWNER_ID": owner_id,
        "OWNER_TYPE_ID": owner_type_id,
        "PROVIDER_ID": provider_id,
        "PROVIDER_TYPE_ID": provider_type_id,
        "SUBJECT": subject,
        "RESPONSIBLE_ID": responsible_id,
        "DESCRIPTION": description,
        "COMPLETED": completed,
        "DIRECTION": direction
    }

    # Wywołanie API
    # Używamy metody crm.activity.add
    print("Dane aktywności: ", fields)

    # Zakładam użycie standardowego WEBHOOK_URL, chyba że masz dedykowany dla Activity
    url_base = getattr(BitrixConfig, 'NEW_WEBHOOK_URL', BitrixConfig.NEW_WEBHOOK_URL)

    result = bitrix_call(url_base, "crm.activity.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Sukces! Dodano aktywność. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania aktywności: {result}")
        return None


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
    result = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.contact.add.json", {"fields": fields})

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
    result = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.company.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Sukces! Dodano nową firmę. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania firmy: {result}")
        return None


def add_new_lead(
        first_name,         # Imię
        last_name,          # Nazwisko
        phone=None,         # Telefon
        contact_id=None,    # ID powiązanego kontaktu
        assigned_by_id=161, # ID osoby odpowiedzialnej (domyślnie 161)
):
    """
    Tworzy nowego leada w Bitrix24.
    """
    full_name = f"{first_name} {last_name}"
    print(f"🚀 Wysyłam dane dla leada: {full_name}...")

    fields = {
        "NAME": first_name,
        "LAST_NAME": last_name,
        "TITLE": full_name,
        "SOURCE_ID": "CALL",
        "OPENED": "Y",
        "ASSIGNED_BY_ID": assigned_by_id,
    }

    if contact_id:
        fields["CONTACT_ID"] = contact_id

    if phone:
        fields["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "MOBILE"}]

    print("Pola leada: ", fields)
    result = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.lead.add.json", {"fields": fields})

    if "result" in result:
        new_id = result["result"]
        print(f"✅ Sukces! Dodano nowego leada. ID: {new_id}")
        return new_id
    else:
        print(f"❌ Błąd podczas dodawania leada: {result}")
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

        r = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.company.list.json", params)

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
                    update_res = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.company.update.json", {
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


# --- NOWA FUNKCJA: Pobieranie telefoni ---

def parse_source_from_tgstack(tg_stack_str):
    """
    Sprawdza, czy w TG_STACK znajduje się numer ze słownika SOURCE_NUMBERS.
    """
    if not tg_stack_str or "Error" in tg_stack_str or "Not Found" in tg_stack_str:
        return ""
    first_part = tg_stack_str.split(',')[0].strip()
    for number, label in TelephonyConfig.SOURCE_NUMBERS.items():
        if number in first_part:
            return label
    return ""


def map_portal_number(portal_number_str):
    """
    Sprawdza czy portal_number_str (np. 'reg66825') istnieje w słowniku PORTAL_NUMBERS.
    Jeśli tak, zwraca przypisaną wartość (np. '4822626392').
    """
    if not portal_number_str:
        return ""

    # Pobieramy wartość ze słownika, jeśli klucza nie ma, zwraca pusty string
    return TelephonyConfig.PORTAL_NUMBERS.get(str(portal_number_str), "")


def get_last_id_from_csv(filename, default_start=0):
    if not os.path.exists(filename):
        print(f"ℹ️ Plik {filename} nie istnieje. Zaczynam od domyślnego ID: {default_start}")
        return default_start
    max_id = 0
    try:
        with open(filename, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row_val = row.get("ID", 0)
                    if row_val:
                        current_id = int(row_val)
                        if current_id > max_id:
                            max_id = current_id
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        print(f"⚠️ Błąd odczytu ostatniego ID z pliku: {e}")
        return default_start
    final_id = max(max_id, default_start)
    print(f"ℹ️ Ostatnie znalezione ID w pliku: {max_id}. Użyję w filtrze > {final_id}")
    return final_id


def extract_tgstack_from_log_url(log_url):
    if not log_url:
        return ""
    try:
        response = requests.get(log_url, timeout=10)
        response.raise_for_status()
        content = response.content.decode('utf-8', errors='replace')
        for line in content.splitlines():
            clean_line = line.strip()
            if "X-CTMG-TGStack" in clean_line:
                parts = clean_line.split(":", 1)
                if len(parts) > 1:
                    return parts[1].strip()
        return "Not Found"
    except Exception as e:
        return f"Error: {str(e)[:50]}"


def fetch_telephony_stats_with_logs(csv_filename):
    """
    Pobiera statystyki połączeń, logi, źródła (TG_STACK) i mapuje PORTAL_NUMBER.
    Zawiera logikę 'Seeking': jeśli pobrana strona zawiera tylko stare ID,
    pomija ją i szuka dalej, inkrementując parametr start.
    """
    last_known_id = get_last_id_from_csv(csv_filename, default_start=0)
    print(f">>> Rozpoczynam pobieranie telefonii. Ostatnie znane ID: {last_known_id}")

    file_exists = os.path.exists(csv_filename)

    fieldnames = [
        "ID",
        "CRM_ENTITY_ID",
        "PORTAL_NUMBER",
        "PORTAL_MAPPED",
        "PHONE_NUMBER",
        "CALL_START_DATE",
        "SOURCE",
        "TG_STACK",
        "CALL_LOG_URL"
    ]

    with open(csv_filename, "a" if file_exists else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        total_processed = 0

        # Sugeruję zacząć od 0, jeśli polegamy na filtrze,
        # ale logika poniżej obsłuży "doganianie" jeśli API zignoruje filtr.
        start = 2350

        step = 50  # Standardowa wielkość strony w Bitrix

        while True:
            params = {
                "order": {"ID": "ASC"},
                "filter": {">ID": last_known_id},  # Filtr API (optymalizacja po stronie serwera)
                "select": ["ID", "CRM_ENTITY_ID", "PORTAL_NUMBER", "PHONE_NUMBER", "CALL_START_DATE", "CALL_LOG",],
                "start": start
            }

            print(f"🔍 Pobieram stronę: start={start} ...")
            r = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "voximplant.statistic.get.json", params)

            if "error" in r:
                print(f"❌ Błąd API: {r}")
                break

            batch = r.get("result", [])

            # --- LOGIKA SEEKING (Szukanie nowych ID) ---
            if not batch:
                print("--- Brak danych (pusta tablica result) - koniec ---")
                break

            # Wyciągamy wszystkie ID z obecnej paczki
            try:
                batch_ids = [int(item.get("ID", 0)) for item in batch]
                max_batch_id = max(batch_ids) if batch_ids else 0
            except ValueError:
                max_batch_id = 0

            # KLUCZOWA ZMIANA:
            # Jeśli maksymalne ID w tej paczce jest mniejsze lub równe temu, co już mamy,
            # to znaczy, że jesteśmy "za nisko" w historii. Przeskakujemy dalej.
            if max_batch_id <= last_known_id:
                print(
                    f"⏩ Strona zawiera tylko stare dane (Max ID w paczce: {max_batch_id} <= Ostatnie znane: {last_known_id}).")

                # Sprawdzenie czy to koniec danych w ogóle
                if len(batch) < step and "next" not in r:
                    print("--- Dotarto do końca danych (brak parametru next i niepełna paczka), brak nowych ID. ---")
                    break

                print(f"   Inkrementuję start o {step} i szukam dalej...")
                start += step
                continue  # Wracamy na początek pętli while, pomijając przetwarzanie

            # --- KONIEC LOGIKI SEEKING ---

            # Jeśli doszliśmy tutaj, to w paczce są jakieś nowe rekordy (przynajmniej jeden)
            rows_to_save = []
            print(f"📥 Pobrano {len(batch)} rekordów. Przetwarzanie (nowe ID > {last_known_id})...")

            for item in batch:
                call_id = int(item.get("ID", 0))

                # Dodatkowe zabezpieczenie: przetwarzamy tylko faktycznie nowsze rekordy
                if call_id <= last_known_id:
                    continue

                lead_id = item.get("CRM_ENTITY_ID")
                call_log_url = item.get("CALL_LOG", "")
                portal_raw = item.get("PORTAL_NUMBER", "")

                portal_mapped_value = map_portal_number(portal_raw)
                tg_stack_value = extract_tgstack_from_log_url(call_log_url)
                source_label = parse_source_from_tgstack(tg_stack_value)

                row = {
                    "ID": call_id,
                    "CRM_ENTITY_ID": lead_id,
                    "PORTAL_NUMBER": portal_raw,
                    "PORTAL_MAPPED": portal_mapped_value,
                    "PHONE_NUMBER": item.get("PHONE_NUMBER", ""),
                    "CALL_START_DATE": item.get("CALL_START_DATE", ""),
                    "SOURCE": source_label,
                    "TG_STACK": tg_stack_value,
                    "CALL_LOG_URL": call_log_url
                }
                rows_to_save.append(row)

            if rows_to_save:
                writer.writerows(rows_to_save)
                f.flush()
                total_processed += len(rows_to_save)
                print(f"✅ Zapisano {len(rows_to_save)} nowych rekordów. Łącznie w sesji: {total_processed}")
            else:
                print("ℹ️ Pobrana paczka zawierała dane, ale wszystkie zostały odfiltrowane (duplikaty ID?).")

            # Obsługa paginacji
            if "next" in r:
                start = r["next"]
            else:
                print("--- Koniec danych (brak parametru 'next') ---")
                break


# ----- Funkcja do fakturacji -----

# --- KROK 3: Pobieranie Dealów (Szans sprzedaży) według kategorii ---

def fetch_deal_categories():
    """
    Pobiera listę dostępnych kategorii (lejków sprzedaży) z CRM.
    Uwaga: Metoda crm.dealcategory.list zwraca tylko niestandardowe lejki.
    Domyślny lejek ogólny ma zawsze ID = 0 i trzeba go uwzględnić ręcznie.
    """
    print(">>> Pobieranie listy kategorii dealów (lejków)...")
    categories = []

    # Domyślny lejek w Bitrix24 zawsze ma ID 0, ale endpoint .list często go nie zwraca.
    # Dodajemy go ręcznie, aby pobrać też deale z głównego widoku.
    categories.append({"ID": 0, "NAME": "Domyślny (Ogólny)"})

    start = 0
    while True:
        params = {
            "order": {"ID": "ASC"},
            "select": ["ID", "NAME"],
            "start": start
        }

        # Używamy Twojej konfiguracji URL (zakładam, że dealcategory jest pod standardowym endpointem lub głównym webhookiem)
        r = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.dealcategory.list.json", params)

        if "error" in r:
            print(f"Błąd pobierania kategorii: {r}")
            break

        batch = r.get("result", [])
        if not batch:
            break

        categories.extend(batch)

        if "next" not in r:
            break
        start = r["next"]

    print(f"✅ Znaleziono łącznie {len(categories)} kategorii (w tym domyślną).")
    return categories


def fetch_deals_by_category(category_id, category_name="Nieznana"):
    """
    Pobiera wszystkie deale należące do konkretnego ID kategorii.
    """
    print(f"🔍 Pobieranie dealów dla kategorii ID: {category_id} ({category_name})...")

    deals = []
    start = 0
    total_fetched = 0

    while True:
        params = {
            "order": {"ID": "ASC"},
            "filter": {"CATEGORY_ID": category_id},  # Filtrowanie po kategorii zgodnie z życzeniem
            "select": ["*", "UF_*"],  # Pobieramy wszystkie pola standardowe i customowe
            "start": start
        }

        r = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.deal.list.json", params)

        if "error" in r:
            print(f"❌ Błąd pobierania dealów dla kat. {category_id}: {r}")
            break

        batch = r.get("result", [])
        if not batch:
            break

        # Opcjonalnie: Możemy dodać nazwę kategorii do każdego rekordu, żeby w CSV było wiadomo skąd pochodzi
        for deal in batch:
            deal['CATEGORY_NAME_EXPORT'] = category_name
            deals.append(deal)

        batch_count = len(batch)
        total_fetched += batch_count
        # print(f"   Pobrano partię {batch_count} dealów...")

        if "next" in r:
            start = r["next"]
        else:
            break

    print(f"   Zakończono kategorię {category_id}. Pobrano łącznie: {total_fetched} dealów.")
    return deals


def process_all_deals_by_categories(output_file):
    """
    1. Pobiera listę kategorii.
    2. Dla każdej kategorii pobiera listę dealów.
    3. Zapisuje wszystko do jednego pliku CSV.
    """
    categories = fetch_deal_categories()
    all_deals_accumulated = []

    for cat in categories:
        c_id = cat.get("ID")
        c_name = cat.get("NAME", str(c_id))

        cat_deals = fetch_deals_by_category(c_id, c_name)
        all_deals_accumulated.extend(cat_deals)

    print(f"\n📊 Podsumowanie: Pobrano łącznie {len(all_deals_accumulated)} dealów ze wszystkich kategorii.")

    if all_deals_accumulated:

        all_deals_accumulated = generate_custom_signatures(all_deals_accumulated)
        save_to_csv(output_file, all_deals_accumulated)
    else:
        print("Brak dealów do zapisania.")


def generate_custom_signatures(deals_list):
    """
    Przetwarza listę dealów i dodaje pole 'CUSTOM_SIGNATURE' w formacie:
    NUMER WŁASNY/KLIENT/ŹRÓDŁO/DZIAŁ/NR HANDLOWCA/DZIEŃ/MIESIĄC/ROK
    """
    print(">>> Generowanie numerów własnych (NK/SK z flagi) i mapowanie źródeł...")

    # --- KONFIGURACJA ŹRÓDEŁ ---
    # Tutaj możesz prosto dopisywać nowe mapowania.
    # Klucz (lewa strona) to SOURCE_ID z systemu, Wartość (prawa) to tekst w sygnaturze.
    SOURCE_MAPPING = {
        'ADVERTISING': 'R',
        'CALL': 'T',
        'EMAIL': '@',
        'WEB': 'S',  # Sugeruję unikać spacji w sygnaturach (np. zamiast "strona internetowa")
        # 'STORE': 'SKLEP',   <- przykład dodania nowego
    }

    # Sortowanie nie jest już krytyczne dla logiki NK/SK (bo mamy flagę),
    # ale można je zostawić dla porządku w pliku wynikowym.
    deals_list.sort(key=lambda x: int(x.get('ID', 0)))

    for deal in deals_list:
        # --- A. Logika NK / SK (na podstawie IS_RETURN_CUSTOMER) ---
        # Zakładamy, że Y = Stary Klient (SK), N = Nowy Klient (NK)
        is_return = deal.get('IS_RETURN_CUSTOMER', 'N')

        if is_return == 'Y':
            client_code = "SK"
        else:
            client_code = "NK"

        # --- B. Mapowanie Źródła (SOURCE_ID) ---
        source_id = deal.get('SOURCE_ID', '')
        # Pobieramy nazwę ze słownika. Jeśli nie ma klucza, używamy "INNE" lub samego ID.
        source_code = SOURCE_MAPPING.get(source_id, 'INNE')

        # --- C. Pobieranie Daty (CLOSEDATE) ---
        close_date_raw = deal.get('CLOSEDATE', '')
        day, month, year = "00", "00", "0000"

        if close_date_raw:
            date_part = str(close_date_raw)[:10]
            try:
                y_temp, m_temp, d_temp = date_part.split('-')
                day, month, year = d_temp, m_temp, y_temp
            except ValueError:
                pass

        # --- D. Reszta pól ---
        deal_id = deal.get('ID', '')
        category_id = deal.get('CATEGORY_ID', '0')
        created_by = deal.get('CREATED_BY_ID', '0')

        # --- E. Złożenie ciągu ---
        # Wzór: ID/KLIENT/ŹRÓDŁO/DZIAŁ/NR HANDLOWCA/DZIEŃ/MIESIĄC/ROK
        signature = f"{deal_id}/{client_code}/{source_code}/{category_id}/{created_by}/{day}/{month}/{year}"

        deal['GENERATED_SIGNATURE'] = signature

    return deals_list


def export_invoice_legends():
    """
        Pobiera listę pracowników oraz kategorie dealów (lejki) i zapisuje je
        do JEDNEGO pliku CSV jako legendę do faktur.
        Format pliku: TYP, ID, NAZWA
        """
    print("\n--- GENEROWANIE WSPÓLNEJ LEGENDY (Pracownicy + Lejki) ---")

    combined_rows = []

    # --- 1. Pobieranie Pracowników ---
    users = fetch_all_users()
    for u in users:
        # Sklejamy imię i nazwisko
        full_name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
        # Jeśli brak imienia, bierzemy email lub login
        if not full_name:
            full_name = u.get('EMAIL', u.get('LOGIN', 'Nieznany'))

        combined_rows.append({
            "TYP": "PRACOWNIK",
            "ID": u.get("ID"),
            "NAZWA": full_name
        })

    # --- 2. Pobieranie Kategorii Dealów (tzw. Branże/Lejki) ---
    categories = fetch_deal_categories()
    for cat in categories:
        combined_rows.append({
            "TYP": "BRANZA_LEJEK",  # Możesz tu wpisać "KATEGORIA" jeśli wolisz
            "ID": cat.get("ID"),
            "NAZWA": cat.get("NAME")
        })

    # --- 3. Zapis do jednego pliku ---
    if combined_rows:
        filename = "legenda_faktury_wspolna.csv"
        # Sortujemy dla porządku: najpierw po TYPIE, potem po ID
        combined_rows.sort(key=lambda x: (x["TYP"], int(x["ID"]) if str(x["ID"]).isdigit() else x["ID"]))

        save_to_csv(filename, combined_rows)
        print(f"✅ Wygenerowano plik: {filename} ({len(combined_rows)} pozycji)")
    else:
        print("⚠️ Brak danych do zapisania.")

#Deale i telefonia
def sanitize_filename(name):
    """
    Pomocnicza funkcja czyszcząca nazwę działu z niedozwolonych znaków dla systemu plików.
    """
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip().replace(" ", "_")


def fetch_deals_by_category_with_date(category_id, category_name, start_date_iso):
    """
    Pobiera deale dla wskazanej kategorii utworzone po określonej dacie.
    """
    print(f"🔍 Pobieranie dealów dla kategorii ID: {category_id} ({category_name}) od daty {start_date_iso}...")

    deals = []
    start = 0
    total_fetched = 0

    while True:
        params = {
            "order": {"ID": "ASC"},
            "filter": {
                "CATEGORY_ID": category_id,
                ">=BEGINDATE": start_date_iso  # Czysty string, bez kodowania URL!
            },
            "select": ["*", "UF_*"],
            "start": start
        }

        r = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.deal.list.json", params)

        if "error" in r:
            print(f"❌ Błąd pobierania dealów dla kat. {category_id}: {r}")
            break

        batch = r.get("result", [])
        if not batch:
            break

        for deal in batch:
            deal['CATEGORY_NAME_EXPORT'] = category_name
            deals.append(deal)

        batch_count = len(batch)
        total_fetched += batch_count

        if "next" in r:
            start = r["next"]
        else:
            break

    print(f"   Zakończono kategorię {category_id}. Pobrano łącznie: {total_fetched} dealów.")
    return deals


def enrich_deals_with_phones(deals_list):
    """
    Wyciąga ID kontaktów i firm z listy dealów, pobiera dla nich numery telefonów
    w paczkach (zbiorczo) i dodaje do każdego deala pole 'CLIENT_PHONE'.
    """
    print(">>> Mapowanie numerów telefonów do pobranych dealów...")

    contact_ids = set()
    company_ids = set()

    # Zbieramy unikalne ID kontaktów i firm
    for deal in deals_list:
        cid = deal.get("CONTACT_ID")
        if cid:
            contact_ids.add(cid)

        comp_id = deal.get("COMPANY_ID")
        if comp_id:
            company_ids.add(comp_id)

    phone_map_contacts = {}
    phone_map_companies = {}

    # Pobieranie telefonów dla KONTAKTÓW (w paczkach po 50)
    if contact_ids:
        c_list = list(contact_ids)
        for i in range(0, len(c_list), 50):
            chunk = c_list[i:i + 50]
            # Operator '@ID' w filtrze pozwala wyszukać wiele ID naraz
            params = {"filter": {"@ID": chunk}, "select": ["ID", "PHONE"]}
            res = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.contact.list.json", params)

            for contact in res.get("result", []):
                phone_str = flatten_multifield(contact.get("PHONE"))
                phone_map_contacts[str(contact["ID"])] = phone_str

    # Pobieranie telefonów dla FIRM (w paczkach po 50)
    if company_ids:
        comp_list = list(company_ids)
        for i in range(0, len(comp_list), 50):
            chunk = comp_list[i:i + 50]
            params = {"filter": {"@ID": chunk}, "select": ["ID", "PHONE"]}
            res = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.company.list.json", params)

            for company in res.get("result", []):
                phone_str = flatten_multifield(company.get("PHONE"))
                phone_map_companies[str(company["ID"])] = phone_str

    # Doklejanie telefonów do dealów
    for deal in deals_list:
        cid = str(deal.get("CONTACT_ID", ""))
        comp_id = str(deal.get("COMPANY_ID", ""))

        phone = ""
        # Priorytet ma telefon bezpośrednio z kontaktu. Jeśli go nie ma, bierzemy z firmy.
        if cid and phone_map_contacts.get(cid):
            phone = phone_map_contacts[cid]
        elif comp_id and phone_map_companies.get(comp_id):
            phone = phone_map_companies[comp_id]

        # Zapisujemy telefon pod nowym kluczem, który trafi do CSV
        deal["CLIENT_PHONE"] = phone

    return deals_list


def process_deals_separate_csv_from_date(start_date_iso):
    """
    1. Pobiera listę kategorii.
    2. Pobiera deale od konkretnej daty dla każdej kategorii.
    3. Zapisuje każdy dział do OSOBNEGO pliku CSV.
    """
    print(f"\n--- POBIERANIE DEALÓW OD DATY: {start_date_iso} DO OSOBNYCH PLIKÓW ---")
    categories = fetch_deal_categories()

    total_exported_deals = 0

    for cat in categories:
        c_id = cat.get("ID")
        c_name = cat.get("NAME", f"Kategoria_{c_id}")

        # Pobieramy deale dla tego jednego działu z filtrem daty
        cat_deals = fetch_deals_by_category_with_date(c_id, c_name, start_date_iso)

        if cat_deals:
            # Używamy Twojej funkcji do generowania podpisów z flagami NK/SK
            cat_deals = generate_custom_signatures(cat_deals)

            # Tworzymy bezpieczną nazwę pliku
            cat_deals = enrich_deals_with_phones(cat_deals)

            safe_c_name = sanitize_filename(c_name)
            filename = f"deals_{start_date_iso[:10]}_dzial_{c_id}_{safe_c_name}.csv"

            save_to_csv(filename, cat_deals)
            total_exported_deals += len(cat_deals)
            print(f"✅ Zapisano plik: {filename} (rekordów: {len(cat_deals)})\n")
        else:
            print(f"ℹ️ Pominięto dział '{c_name}' - brak dealów spełniających kryteria.\n")

    print(f"📊 PODSUMOWANIE: Zapisano łącznie {total_exported_deals} dealów w osobnych plikach CSV.")


def clean_phone_number(phone):
    """
    Usuwa WSZYSTKIE znaki niebędące cyframi.
    Usuwa '+', spacje, myślniki, nawiasy.
    Zmienia: "+48 500-123-456" -> "48500123456"
    Zmienia: "48500123456"     -> "48500123456"
    """
    if not phone:
        return ""
    # [^0-9] oznacza: znajdź wszystko co nie jest cyfrą i zamień na pusty string
    return re.sub(r"[^0-9]", "", str(phone))


def find_owner_by_incoming_sms_old(mongo_uri, db_name, collection_name):
    """
    1. Pobiera ostatni SMS z Mongo.
    2. Szuka w historii Bitrixa (CRM_SMS), do jakiego Deala/Leada (OWNER_ID)
       wysyłaliśmy wiadomość na ten numer.
    """

    # --- KROK 1: Pobranie numeru z MongoDB ---
    print(">>> 1. Łączenie z MongoDB...")
    try:
        client = MongoClient(mongo_uri)
        db = client[db_name]
        collection = db[collection_name]

        # Pobierz JEDEN najnowszy rekord
        last_sms = collection.find_one(sort=[("received_at", DESCENDING)])

        if not last_sms:
            print("❌ Błąd: Nie znaleziono żadnych wiadomości w bazie MongoDB.")
            return None

        # Pobieramy numer nadawcy z Mongo
        raw_sms_from = last_sms.get('sms_from')
        clean_sms_from = clean_phone_number(raw_sms_from)

        print(f"✅ Znaleziono w Mongo najnowszy SMS od: {raw_sms_from} (Clean: {clean_sms_from})")

    except Exception as e:
        print(f"❌ Błąd połączenia z MongoDB: {e}")
        return None

    # --- KROK 2: Pobranie aktywności z Bitrix24 ---
    print(f">>> 2. Szukanie pasującego Deal'a w Bitrix24 dla numeru {clean_sms_from}...")

    # Pobieramy tylko aktywności typu SMS, sortując od najnowszych (ID DESC),
    # żeby znaleźć ostatnią interakcję.
    params = {
        "order": {"ID": "DESC"},
        "filter": {
            "PROVIDER_ID": "CRM_SMS",
        },
        "select": ["ID", "OWNER_ID", "OWNER_TYPE_ID", "SETTINGS", "SUBJECT", "created"]
    }

    # Używamy Twojej funkcji bitrix_call (zakładam, że jest dostępna w scope)
    # Jeśli nie, podmień BitrixConfig.WEBHOOK_URL na swój URL
    result = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.activity.list.json", params)

    if "error" in result:
        print(f"❌ Błąd API Bitrix: {result}")
        return None

    activities = result.get("result", [])

    # --- KROK 3: Porównywanie numerów ---

    for activity in activities:
        # Bezpieczne wejście w zagnieżdżoną strukturę JSON-a
        settings = activity.get("SETTINGS", {})
        # Struktura może być listą (pustą) lub słownikiem, w JSON z przykładu jest słownikiem gdy ma dane
        if isinstance(settings, list):
            continue

        original_msg = settings.get("ORIGINAL_MESSAGE", {})
        bitrix_message_to = original_msg.get("MESSAGE_TO", "")

        # Czyścimy numer z Bitrixa
        clean_bitrix_to = clean_phone_number(bitrix_message_to)

        # Porównujemy numery
        # Sprawdzamy czy numer z Mongo (sms_from) jest taki sam jak ten, do którego pisaliśmy (message_to)
        if clean_bitrix_to and clean_bitrix_to == clean_sms_from:
            owner_id = activity.get("OWNER_ID")
            owner_type_id = activity.get("OWNER_TYPE_ID")

            print(f"✅ SUKCES! Znaleziono dopasowanie.")
            print(f"   Aktywność ID: {activity['ID']}")
            print(f"   Numer w Bitrix: {bitrix_message_to}")
            print(f"   OWNER_ID (Deal/Lead): {owner_id}")
            print(f"   OWNER_TYPE_ID: {owner_type_id}")

            return {
                "OWNER_ID": owner_id,
                "OWNER_TYPE_ID": owner_type_id
            }

    print("⚠️ Nie znaleziono w Bitrix aktywności SMS wysłanej na ten numer telefonu.")
    return None

def find_owner_by_incoming_sms(phone_number): #used in actual API when receiving SMS
    """
    Szuka w historii Bitrixa (CRM_SMS), do jakiego Deala/Leada (OWNER_ID)
    wysyłaliśmy wiadomość na ten numer.
    Nie łączy się z Mongo - przyjmuje numer prosto z requestu.
    """
    clean_sms_from = clean_phone_number(phone_number)
    print(f">>> Szukanie pasującego Deal'a w Bitrix24 dla numeru {clean_sms_from}...")

    if not clean_sms_from:
        print("❌ Pusty numer telefonu po czyszczeniu.")
        return None

    # Pobieramy aktywności SMS, sortując od najnowszych
    # Dodalem RESPONSIBLE_ID do select, żeby wiedzieć kto opiekował się klientem
    params = {
        "order": {"ID": "DESC"},
        "filter": {
            "PROVIDER_ID": "CRM_SMS",
        },
        "select": ["ID", "OWNER_ID", "OWNER_TYPE_ID", "SETTINGS", "SUBJECT", "RESPONSIBLE_ID"]
    }

    result = bitrix_call(BitrixConfig.NEW_WEBHOOK_URL, "crm.activity.list.json", params)

    if "error" in result:
        print(f"❌ Błąd API Bitrix: {result}")
        return None

    activities = result.get("result", [])

    for activity in activities:
        settings = activity.get("SETTINGS", {})

        # Zabezpieczenie przed pustą listą w settings (bug Bitrixa)
        if isinstance(settings, list):
            continue

        original_msg = settings.get("ORIGINAL_MESSAGE", {})
        bitrix_message_to = original_msg.get("MESSAGE_TO", "")

        clean_bitrix_to = clean_phone_number(bitrix_message_to)

        # Porównujemy numer z Bitrixa z numerem przychodzącym
        if clean_bitrix_to and clean_bitrix_to == clean_sms_from:
            owner_id = activity.get("OWNER_ID")
            owner_type_id = activity.get("OWNER_TYPE_ID")
            responsible_id = activity.get("RESPONSIBLE_ID")  # Pobieramy opiekuna

            print(f"✅ SUKCES! Znaleziono dopasowanie.")
            print(f"   Aktywność ID: {activity['ID']}")
            print(f"   OWNER_ID: {owner_id} (Typ: {owner_type_id})")
            print(f"   Opiekun: {responsible_id}")

            return {
                "OWNER_ID": owner_id,
                "OWNER_TYPE_ID": owner_type_id,
                "RESPONSIBLE_ID": responsible_id
            }

    print("⚠️ Nie znaleziono w Bitrix aktywności SMS wysłanej na ten numer telefonu.")
    return None


def add_external_call_with_recording(phone_number, user_id, record_url, duration=60, entity_type=None, entity_id=None):
    """
    Rejestruje połączenie i podpina pod nie link do nagrania.
    Jeśli podano entity_type i entity_id, przypisuje nagranie do konkretnego rekordu.
    W przeciwnym razie Bitrix szuka po numerze telefonu lub tworzy nowy Lead.
    """
    print(f"--- PROCESOWANIE NAGRANIA DLA: {phone_number} ---")

    reg_params = {
        "USER_ID": user_id,
        "PHONE_NUMBER": phone_number,
        "TYPE": 2,
        "SHOW": 0
    }

    # Jeśli przekazaliśmy konkretne ID z zewnątrz:
    if entity_type and entity_id:
        reg_params["CRM_CREATE"] = 0
        reg_params["CRM_ENTITY_TYPE"] = entity_type
        reg_params["CRM_ENTITY_ID"] = entity_id
        print(f"📌 Wymuszono przypisanie do: {entity_type} [{entity_id}]")
    else:
        # Domyślne zachowanie (szukaj lub twórz)
        reg_params["CRM_CREATE"] = 1

    reg_result = bitrix_call(BitrixConfig.AUTOBOT_WEBHOOK, "telephony.externalcall.register", reg_params)

    if "result" in reg_result:
        call_id = reg_result["result"]["CALL_ID"]
        crm_entity_id = reg_result["result"].get("CRM_ENTITY_ID")
        print(f"✅ Połączenie zarejestrowane. CALL_ID: {call_id}, Lead/Entity ID: {crm_entity_id}")

        # KROK 2: Zakończenie połączenia i wysłanie linku do nagrania
        # Bitrix pobierze plik z RECORD_URL i umieści go na swoim serwerze/dysku
        finish_params = {
            "CALL_ID": call_id,
            "USER_ID": user_id,
            "DURATION": duration,  # Długość w sekundach
            "RECORD_URL": record_url  # KLUCZOWE: Bezpośredni link do pliku .mp3 / .wav
        }

        finish_result = bitrix_call(BitrixConfig.AUTOBOT_WEBHOOK, "telephony.externalcall.finish", finish_params)

        if "result" in finish_result:
            print("✅ Nagranie zostało pomyślnie przekazane do Bitrix24.")
        else:
            print("❌ Błąd podczas przesyłania nagrania:", finish_result)

        return finish_result
    else:
        print("❌ Błąd rejestracji połączenia:", reg_result)
        return reg_result

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
    print("9. Pobierz statystyki telefonii + dane z logów (Incremental)")
    print("10. Pobierz DEALE według kategorii do CSV")
    print("11. Dodaj testową aktywność (SMS odebrany)")  # <--- NOWA OPCJA
    print("12. Pobierz owner id itd")
    print("13. Pobierz DEALE do OSOBNYCH CSV od wybranej daty")
    print("14. lead")
    print("15. Test Metody 2: Nagranie z zewnętrznego linku (Telefonia)")  #

    choice = input("Wybierz opcję (1/13): ").strip()

    if choice == "1":
        FILE_FINAL = "companies_full_export.csv"
        companies_data = fetch_all_companies_optimized(5700)
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

    elif choice == "9":
        TELEPHONY_FILE = "telephony_stats_full.csv"
        fetch_telephony_stats_with_logs(TELEPHONY_FILE)

    elif choice == "10":
        # --- NOWA OPCJA ---
        DEALS_FILE = "deals_full_export.csv"
        process_all_deals_by_categories(DEALS_FILE)
        export_invoice_legends()

    elif choice == "11":
        # --- NOWA OPCJA: Dodawanie aktywności ---
        add_new_activity(
            owner_id="2713",  # Twoje OWNER_ID
            owner_type_id="2",  # Twoje OWNER_TYPE_ID (2 = Deal)
            responsible_id="223",  # Twoje RESPONSIBLE_ID
            subject="SMS odebrany",  # Twoje SUBJECT
            description="[p]\nTest - Python\n[/p]",  # Twoje DESCRIPTION
            provider_id="CRM_TODO",
            provider_type_id="TODO"
        )

    elif choice == "12":

        MONGO_URL = "mongodb+srv://europajena_db_user:dJZk2TmRjIh0Apaj@jenaeuropa.qnfx36r.mongodb.net/"
        DB_NAME = "TelefoniaAPI"
        COLLECTION = "smsReceived"

        found_data = find_owner_by_incoming_sms_old(MONGO_URL, DB_NAME, COLLECTION)

        if found_data:
            # Tutaj możesz wywołać swoją funkcję add_new_activity
            print("Można dodać notatkę do:", found_data)



    elif choice == "13":
        # Możesz zmienić datę poniżej na dowolną inną
        TARGET_DATE = "2024-12-15T00:00:00+03:00"
        #TARGET_DATE = "2026-03-20T00:00:00+03:00"
        process_deals_separate_csv_from_date(TARGET_DATE)

    elif choice == "14":
        add_new_lead(
            first_name="marko",
            last_name="KOPACZ",
            phone="696969123",
            contact_id=20393,
            assigned_by_id=161,
        )

    elif choice == "15":
        # DANE TESTOWE
        TEST_PHONE = "888 788 525"
        TEST_USER_ID = 173  # Używam ID z Twojego przykładu add_new_lead
        # WAŻNE: Link musi być bezpośredni i publicznie dostępny dla serwera Bitrix
        TEST_RECORD_URL = ""

        add_external_call_with_recording(
            phone_number=TEST_PHONE, #Phone
            user_id=TEST_USER_ID, #Responsible
            record_url=TEST_RECORD_URL, #link
            duration=45, #useless
            entity_type="LEAD", #find out if lead or deal
            entity_id=20897 #id of lead/deal
        )

    else:
        print("Nieprawidłowy wybór.")


if __name__ == "__main__":
    main()