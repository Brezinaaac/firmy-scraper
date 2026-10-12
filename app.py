import asyncio
import os
import random
import re
import pandas as pd
import streamlit as st
from playwright.async_api import async_playwright

st.set_page_config(page_title="Firmy.cz Lokální Scraper", page_icon="🔍", layout="wide")

st.title("🔍 Firmy.cz Data Extractor (Trvalý souhlas)")
st.write("Skript si pamatuje odsouhlasení cookies v lokální složce relace.")

with st.sidebar:
    st.header("Nastavení")
    url_input = st.text_input("URL Kategorie", "https://www.firmy.cz/Remesla-a-sluzby/kraj-olomoucky")
    pages_input = st.number_input("Počet stran", min_value=1, max_value=20, value=2, step=1)
    
    show_browser = st.checkbox("Zobrazit okno prohlížeče (doporučeno)", value=True)
    start_button = st.button("🚀 Spustit stahování", type="primary")

async def auto_accept_cookies(page):
    """Speciální funkce pro odchycení CMP lišty i uvnitř iFramů"""
    try:
        # 1. Pokus najít tlačítko v jakémkoliv iFramu na stránce
        for frame in page.frames:
            try:
                for text in ["Souhlasím", "Rozumím", "Přijmout vše", "Povolit vše", "SOUHLASÍM"]:
                    btn = frame.locator(f'button:has-text("{text}"), a:has-text("{text}")').first
                    if await btn.is_visible(timeout=500):
                        await btn.click(force=True)
                        await asyncio.sleep(1)
                        return True
            except Exception:
                pass

        # 2. Odstranění zbytků overlays přes JS
        await page.evaluate("""
            () => {
                const selectors = ['[id*="didomi"]', '[class*="consent"]', '[id*="cmp"]', '.overlay-ad'];
                selectors.forEach(s => {
                    document.querySelectorAll(s).forEach(el => el.remove());
                });
            }
        """)
    except Exception:
        pass
    return False

async def scrape_firmy(category_url, max_pages, headless_mode, status_box, table_placeholder):
    company_links = []
    results = []

    # Složka pro uchování cookies a relace
    user_data_dir = os.path.join(os.getcwd(), "browser_session")

    async with async_playwright() as p:
        # Použití persistentního kontextu (pamatuje si odsouhlasení)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless_mode,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ],
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="cs-CZ",
            viewport={"width": 1440, "height": 900}
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # 1. KROK: Sběr odkazů z kategorií
        for page_num in range(1, max_pages + 1):
            url = f"{category_url}?page={page_num}" if page_num > 1 else category_url
            status_box.info(f"Načítám seznam firem (strana {page_num}/{max_pages})...")
            
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)

                # Pokus o odsouhlasení cookies
                await auto_accept_cookies(page)

                # Proscrollování
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)

                hrefs = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a'))
                        .map(a => a.href)
                        .filter(h => h && h.includes('firmy.cz/detail/'))
                """)
                company_links.extend(hrefs)
            except Exception as e:
                status_box.warning(f"Chyba při načítání strany {page_num}: {e}")
                break

        company_links = list(dict.fromkeys(company_links))
        
        if not company_links:
            status_box.error("⚠️ Nepodařilo se načíst odkaz na žádnou firmu. Pokud vidíte okno s cookies, odklikněte ho prosím ručně v prohlížeči — skript si to zapamatuje pro příště!")
            await context.close()
            return pd.DataFrame(results)

        status_box.success(f"Nalezeno {len(company_links)} unikátních firem. Stahuji detaily...")

        # 2. KROK: Procházení detailů jednotlivých firem
        progress_bar = st.progress(0)
        for idx, link in enumerate(company_links, start=1):
            try:
                status_box.info(f"Zpracovávám firmu [{idx}/{len(company_links)}]...")
                await page.goto(link, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(random.uniform(1.0, 2.0))

                # Odstranění lišt na detailu
                await auto_accept_cookies(page)

                title_el = page.locator('h1').first
                name = await title_el.inner_text() if await title_el.count() > 0 else "N/A"

                try:
                    phone_btn = page.locator('button:has-text("Zobrazit telefon")').first
                    if await phone_btn.is_visible(timeout=500):
                        await phone_btn.click(force=True)
                        await asyncio.sleep(0.5)
                except Exception:
                    pass

                phone_el = page.locator('a[href^="tel:"]').first
                if await phone_el.count() > 0:
                    phone = await phone_el.inner_text()
                else:
                    page_text = await page.locator('body').inner_text()
                    match = re.search(r'(\+?420\s*)?[23456789]\d{2}\s*\d{3}\s*\d{3}', page_text)
                    phone = match.group(0) if match else "N/A"

                email_el = page.locator('a[href^="mailto:"]').first
                email = await email_el.inner_text() if await email_el.count() > 0 else "N/A"

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
                
                df_current = pd.DataFrame(results)
                table_placeholder.dataframe(df_current, use_container_width=True)
                progress_bar.progress(idx / len(company_links))

            except Exception:
                continue

        await context.close()
        return pd.DataFrame(results)

if start_button:
    status_box = st.empty()
    table_placeholder = st.empty()
    
    status_box.info("Spouštím prohlížeč...")
    
    headless_val = not show_browser
    df_results = asyncio.run(scrape_firmy(url_input, pages_input, headless_val, status_box, table_placeholder))
    
    if not df_results.empty:
        status_box.success("🎉 Stahování je úspěšně dokončeno!")
        col1, col2 = st.columns(2)
        
        csv_data = df_results.to_csv(index=False, sep=";", encoding="utf-8-sig")
        col1.download_button("📄 Stáhnout jako CSV", csv_data, "firmy_vystup.csv", "text/csv")
        
        import io
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Firmy')
        col2.download_button("📊 Stáhnout jako Excel", buffer.getvalue(), "firmy_vystup.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")