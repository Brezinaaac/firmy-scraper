import asyncio
import json
import re
import pandas as pd
from playwright.async_api import async_playwright

async def scrape_firmy_cz(category_path: str, max_pages: int = 2):
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="cs-CZ"
        )
        page = await context.new_page()

        print("=== Zahajuji extrakci z HTML struktur Firmy.cz ===")

        for page_num in range(1, max_pages + 1):
            url = f"https://www.firmy.cz/{category_path}?page={page_num}" if page_num > 1 else f"https://www.firmy.cz/{category_path}"
            print(f"Zpracovávám stránku {page_num}: {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1) # Krátký čas pro vykreslení datových značek
            except Exception as e:
                print(f"  ❌ Chyba při načítání stránky: {e}")
                break

            # Získáme všechny JSON-LD skripty na stránce
            json_ld_scripts = await page.locator('script[type="application/ld+json"]').all_inner_texts()
            
            found_count = 0
            for script_content in json_ld_scripts:
                try:
                    data = json.loads(script_content)
                    
                    # Ošetření, zda jde o ItemList (seznam firem na stránce)
                    items = []
                    if isinstance(data, dict):
                        if data.get("@type") == "ItemList":
                            items = data.get("itemListElement", [])
                        elif "@graph" in data:
                            for graph_item in data["@graph"]:
                                if graph_item.get("@type") == "ItemList":
                                    items = graph_item.get("itemListElement", [])
                                    break
                    
                    for item in items:
                        # Položka může být obalena v 'item' nebo přímo
                        firm_data = item.get("item", item) if isinstance(item, dict) else {}
                        if not isinstance(firm_data, dict):
                            continue

                        name = firm_data.get("name", "N/A")
                        phone = firm_data.get("telephone", "N/A")
                        email = firm_data.get("email", "N/A")

                        # Formatování adresy
                        address_obj = firm_data.get("address", {})
                        if isinstance(address_obj, dict):
                            street = address_obj.get("streetAddress", "") or ""
                            city = address_obj.get("addressLocality", "") or ""
                            postcode = address_obj.get("postalCode", "") or ""
                            address_str = f"{street}, {postcode} {city}".strip(", ")
                        else:
                            address_str = str(address_obj)

                        # Pokud jsme našli alespoň název, uložíme
                        if name and name != "N/A":
                            results.append({
                                "Nazev": name,
                                "Telefon": phone,
                                "Email": email,
                                "Adresa": address_str if address_str else "N/A"
                            })
                            found_count += 1

                except json.JSONDecodeError:
                    continue

            print(f"  ✓ Získáno {found_count} firem ze stránky {page_num}.")

        await browser.close()

    # Odstranění duplicit a zápis výsledků
    if results:
        unique_results = []
        seen = set()

        for r in results:
            key = (r['Nazev'], r['Email'], r['Adresa'])
            if key not in seen:
                seen.add(key)
                unique_results.append(r)

        # Zápis do strukturovaného TXT souboru
        with open("vystup_firem.txt", "w", encoding="utf-8") as f:
            for r in unique_results:
                f.write(f"Nazev:{r['Nazev']};Telefon:{r['Telefon']};Email:{r['Email']};Adresa:{r['Adresa']}\n")

        # Zápis do CSV
        df = pd.DataFrame(unique_results)
        df.to_csv("vystup_firem.csv", index=False, sep=";", encoding="utf-8-sig")
        print(f"\n🎉 HOTOVO! Úspěšně uloženo {len(unique_results)} firem do 'vystup_firem.txt' a 'vystup_firem.csv'.")
    else:
        print("\nNebyly nalezeny žádné výsledky.")

if __name__ == "__main__":
    KATEGORIE = "Remesla-a-sluzby/kraj-olomoucky"
    POCET_STRANEK = 2

    asyncio.run(scrape_firmy_cz(KATEGORIE, max_pages=POCET_STRANEK))