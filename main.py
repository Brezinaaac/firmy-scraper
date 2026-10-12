import asyncio
import random
from playwright.async_api import async_playwright
import pandas as pd
import re

async def scrape_firmy_cz(category_url: str, max_pages: int = 2):
    company_links = []
    results = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./browser_session",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="cs-CZ",
            viewport={"width": 1280, "height": 900}
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # 1. ROZŠÍŘENÝ BLOCKER: Blokuje i reklamní sítě Seznamu a ad-servers
        async def safe_blocker(route, request):
            resource_type = request.resource_type
            url = request.url.lower()

            if resource_type in ["image", "media", "font"]:
                return await route.abort()

            ad_domains = [
                "doubleclick.net", "google-analytics.com", "googletagservices.com",
                "adservice.google.com", "adform.net", "gemius.pl", "facebook.net",
                "criteo.com", "pubmatic.com", "rubiconproject.com",
                "ssp.seznam.cz", "im.seznam.cz", "a.gemius.pl"
            ]
            if any(ad in url for ad in ad_domains):
                return await route.abort()

            return await route.continue_()

        await page.route("**/*", safe_blocker)

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        # Pomocná funkce pro skrytí reklamních overlayů a zavření modalů
        async def handle_overlays(p):
            try:
                # Skrytí reklamních bannerů a overlay vrstev pomocí CSS
                await p.add_style_tag(content="""
                    [class*="ad-"], [class*="advert"], [id*="seznam-ad"], 
                    .overlay-ad, .popup-ad, div[id*="zone-"] {
                        display: none !important;
                        pointer-events: none !important;
                    }
                """)
            except Exception:
                pass

            # Pokud vyskočí modal okno/galerie/reklama, zkusíme kliknout na zavírací tlačítko
            close_selectors = [
                'button[aria-label*="Zavřít"]', 
                'button[aria-label*="zavřít"]',
                'button.close', 
                '.modal-close', 
                '[class*="closeButton"]',
                '[class*="close-button"]',
                'button:has-text("Zavřít")'
            ]
            for sel in close_selectors:
                try:
                    btn = p.locator(sel).first
                    if await btn.is_visible(timeout=200):
                        await btn.click(force=True)
                        await asyncio.sleep(0.3)
                except Exception:
                    pass

        print("=== KROK 1: Získávám odkazy na detail firem ===")
        for page_num in range(1, max_pages + 1):
            url = f"{category_url}?page={page_num}" if page_num > 1 else category_url
            print(f"Načítám stránku {page_num}: {url}")
            
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                
                if response and response.status >= 500:
                    print(f"⚠️ Server vrátil chybu {response.status}, čekám 3s a zkouším znovu...")
                    await asyncio.sleep(3)
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)

                await asyncio.sleep(1.5)
                await handle_overlays(page)

                # Odkliknutí cookie lišty
                try:
                    accept_btn = page.locator('button:has-text("Rozumím"), button:has-text("Souhlasím"), button:has-text("Přijmout")').first
                    if await accept_btn.is_visible(timeout=1000):
                        await accept_btn.click()
                except Exception:
                    pass

                # Pomalý scroll dolů pro načtení položek
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                await asyncio.sleep(0.5)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)

                hrefs = await page.evaluate("""
                    () => {
                        const links = Array.from(document.querySelectorAll('a'));
                        return links.map(a => a.href).filter(h => h && h.includes('firmy.cz/detail/'));
                    }
                """)

                print(f"  Na stránce {page_num} nalezeno {len(hrefs)} odkazů.")
                company_links.extend(hrefs)

            except Exception as e:
                print(f"Chyba na stránce {page_num}: {e}")
                break

        company_links = list(dict.fromkeys(company_links))
        print(f"\n🎉 Nalezeno celkem {len(company_links)} unikátních profilů firem ke stažení.\n")

        # --- KROK 2: Procházení detailů ---
        if company_links:
            print("=== KROK 2: Stahuji kontakty z detailů ===")
            for idx, link in enumerate(company_links, start=1):
                try:
                    print(f"[{idx}/{len(company_links)}] Otevírám: {link}")
                    
                    resp = await page.goto(link, wait_until="domcontentloaded", timeout=15000)
                    
                    if resp and resp.status >= 500:
                        print("  ⚠️ SERVER ERROR 500 - Čekám 3 sekundy...")
                        await asyncio.sleep(3)
                        resp = await page.goto(link, wait_until="domcontentloaded", timeout=15000)

                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    await handle_overlays(page)

                    # ZPŘESNĚNÉ SELEKTORY: Pouze tlačítka pro telefon/kontakty (vynechány fotky a galerie)
                    try:
                        show_btns = page.locator(
                            'button:has-text("Zobrazit telefon"), '
                            'button:has-text("Ukázat číslo"), '
                            'button:has-text("Zobrazit číslo"), '
                            'a[href^="tel:"]'
                        )
                        cnt = await show_btns.count()
                        for i in range(cnt):
                            btn = show_btns.nth(i)
                            if await btn.is_visible(timeout=500):
                                await btn.click(force=True)
                                await asyncio.sleep(0.2)
                    except Exception:
                        pass

                    # Název
                    title_el = page.locator('h1').first
                    name = await title_el.inner_text() if await title_el.count() > 0 else "N/A"

                    # Telefon
                    phone_el = page.locator('a[href^="tel:"]').first
                    phone = "N/A"
                    if await phone_el.count() > 0:
                        phone = await phone_el.inner_text()
                    else:
                        page_text = await page.locator('body').inner_text()
                        match = re.search(r'(\+?420\s*)?[23456789]\d{2}\s*\d{3}\s*\d{3}', page_text)
                        phone = match.group(0) if match else "N/A"

                    # E-mail
                    email_el = page.locator('a[href^="mailto:"]').first
                    email = await email_el.inner_text() if await email_el.count() > 0 else "N/A"

                    # Adresa (čištění od "Navigovat")
                    address_el = page.locator('.detailAddress, [class*="address"], address').first
                    address = await address_el.inner_text() if await address_el.count() > 0 else "N/A"
                    address = address.replace("Navigovat", "").strip().replace('\n', ' ')

                    item = {
                        "Nazev": name.strip().replace('\n', ' '),
                        "Telefon": phone.strip(),
                        "Email": email.strip(),
                        "Adresa": address
                    }
                    
                    results.append(item)
                    print(f"  ✓ {item['Nazev']} | Tel: {item['Telefon']} | Email: {item['Email']}")

                except Exception as e:
                    print(f"  ❌ Chyba při načítání detailu: {e}")
                    continue

        await context.close()

    # --- KROK 3: Uložení a vyčištění duplicit ---
    if results:
        cleaned_results = []
        seen = set()

        for r in results:
            key = (r['Nazev'], r['Email'], r['Adresa'])
            if key not in seen:
                seen.add(key)
                cleaned_results.append(r)

        with open("vystup_firem.txt", "w", encoding="utf-8") as f:
            for r in cleaned_results:
                f.write(f"Nazev:{r['Nazev']};Telefon:{r['Telefon']};Email:{r['Email']};Adresa:{r['Adresa']}\n")

        df = pd.DataFrame(cleaned_results)
        df.to_csv("vystup_firem.csv", index=False, sep=";", encoding="utf-8-sig")
        print(f"\n🎉 HOTOVO! Úspěšně uloženo {len(cleaned_results)} firem do 'vystup_firem.txt' a 'vystup_firem.csv'.")

if __name__ == "__main__":
    KATEGORIE_URL = "https://www.firmy.cz/Remesla-a-sluzby/kraj-olomoucky"
    POCET_STRANEK = 2
    
    asyncio.run(scrape_firmy_cz(KATEGORIE_URL, max_pages=POCET_STRANEK))