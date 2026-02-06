import os
import re
import pickle
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import requests
import urllib3
from bs4 import BeautifulSoup

from core import settings
from schemas.customers_scrapper import Customer, TicketItem

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BILLING_COOKIE_FILE = "billing_session.pkl"
MONTH_MAP_ID = {
    "januari": "January", "februari": "February", "maret": "March", "april": "April",
    "mei": "May", "juni": "June", "juli": "July", "agustus": "August",
    "september": "September", "oktober": "October", "november": "November",
    "desember": "December"
}


class BillingScraper:
    def __init__(self, session: Optional[requests.Session] = None, login_url: Optional[str] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        self.reused_session = session is not None
        if not self.reused_session:
            self.login_url = login_url or settings.LOGIN_URL_BILLING
            self._login()

    def _save_cookies(self):
        with open(BILLING_COOKIE_FILE, "wb") as f:
            pickle.dump(self.session.cookies, f)

    def _load_cookies(self) -> bool:
        if os.path.exists(BILLING_COOKIE_FILE):
            with open(BILLING_COOKIE_FILE, "rb") as f:
                self.session.cookies.update(pickle.load(f))
            return True
        return False

    def _is_logged(self) -> bool:
        try:
            r = self.session.get(settings.BILLING_MODULE_BASE, verify=False, allow_redirects=False, timeout=10)
            return r.status_code == 200 and "login" not in r.url.lower()
        except requests.RequestException:
            return False

    def _login(self):
        if self._load_cookies() and self._is_logged():
            return

        payload = {"username": settings.NMS_USERNAME_BILING, "password": settings.NMS_PASSWORD_BILING}
        try:
            r = self.session.post(self.login_url, data=payload, verify=False, timeout=10)
            if r.status_code not in (200, 302) or "login" in r.url.lower():
                raise ConnectionError(f"Billing login failed. Check BILLING credentials and LOGIN_URL_BILLING.")
            self._save_cookies()
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to connect to billing login page: {e}")
        
    @staticmethod
    def _parse_month_year(text: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        if not text:
            return None, None, None
        t = text.strip()
        low = t.lower()
        for indo, eng in MONTH_MAP_ID.items():
            if indo in low:
                t = low.replace(indo, eng).title()
                break
        m = re.search(r'([A-Za-z]+)\s+(\d{4})', t)
        if not m:
            return None, None, None
        mname, y = m.group(1), m.group(2)
        try:
            dt = datetime.strptime(f"{mname} {y}", "%B %Y")
            return m.group(0), dt.month, dt.year
        except Exception:
            return m.group(0), None, None
    
    @staticmethod
    def _parser_whatsapp_url(mobile: str) -> Optional[str]:
        if not mobile:
            return None
        clean_number = mobile.strip()
        if clean_number == "0":
            return None
        
        return f"https://wa.me/{clean_number}"

    @staticmethod
    def _parser_maps_url(coordinate: str) -> Optional[str]:
        if not coordinate:
            return None
        clean_coordinate = coordinate.strip()
        if clean_coordinate == "0":
            return None
        
        return f"https://www.google.com/maps?q={clean_coordinate}"

    def search(self, search_value: str) -> List[Dict]:
        search_payload = {"type_cari": search_value, "cari_tagihan": ""}
        try:
            res = self.session.post(
                settings.BILLING_MODULE_BASE,
                data=search_payload,
                verify=False,
                timeout=15,
                allow_redirects=True 
            )
            res.raise_for_status()
        except requests.RequestException as e:
            raise ConnectionError(f"Search request failed: {e}")

        soup = BeautifulSoup(res.text, "html.parser")
        
        final_url_params = parse_qs(urlparse(res.url).query)
        if 'csp' in final_url_params and 'id' in final_url_params:
            customer_id = final_url_params['id'][0]
            name_tag = soup.select_one("h5.font-size-15.mb-0") 
            address_tag = soup.select_one("p.text-muted.mb-4")
            pppoe_tag = soup.find(lambda tag: 'User PPPoE' in tag.text)
            return [{
                "id": customer_id,
                "name": name_tag.get_text(strip=True) if name_tag else "N/A",
                "address": address_tag.get_text(strip=True) if address_tag else "N/A",
                "user_pppoe": pppoe_tag.find_next_sibling('p').get_text(strip=True) if pppoe_tag else "N/A"
            }]

        table = soup.find("table", id="create_note")
        if not table or not table.tbody:
            return []

        collected_data = []
        for row in table.tbody.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 5:
                continue
            name_tag = cols[0].find("h5")
            address_tag = cols[0].find("p")
            pppoe_tags = cols[1].find_all("p")
            
            # Find detail link - ID can be base64 encoded (not just digits)
            details_link_tag = cols[4].find("a", href=re.compile(r"deusr"))
            if not all([name_tag, address_tag, details_link_tag]) or len(pppoe_tags) < 2:
                continue
            
            # Extract ID, handling whitespace in base64 encoded IDs
            href = details_link_tag.get('href', '')
            match = re.search(r"id=([^\s\"&]+)", href)
            if not match:
                # Try getting everything after id= and strip whitespace
                match = re.search(r"id=\s*(.+)", href, re.DOTALL)
                if match:
                    customer_id = re.sub(r'\s+', '', match.group(1))  # Remove all whitespace
                else:
                    continue
            else:
                customer_id = match.group(1).strip()
            
            collected_data.append({
                "id": customer_id,
                "name": name_tag.get_text(strip=True),
                "address": address_tag.get_text(strip=True),
                "user_pppoe": pppoe_tags[1].get_text(strip=True),
            })
        return collected_data

    def _prime_module(self):
        try:
            module_base = "https://nms.lexxadata.net.id/billing2/04/04101"
            self.session.get(module_base + "/index.php", verify=False, timeout=15)
        except Exception:
            pass

    def _find_modal_for_li(self, li, soup):
        btn = li.select_one('button[data-target]')
        if not btn:
            return None
        target_id = (btn.get("data-target") or "").lstrip("#").strip()
        if not target_id:
            return None
        return soup.select_one(f"#{target_id}")

    def _extract_from_textarea(self, ta_text: str) -> dict:
        if not ta_text:
            return {}
        text = re.sub(r'\r', '', ta_text).strip()
        m_name = re.search(r'^\s*Nama\s*:\s*(.+)$', text, re.M)
        customer_name = (m_name.group(1).strip() if m_name else (re.search(r'Pelanggan Yth,\s*\*(.*?)\*', text) or [None, None])[1])
        m_no = re.search(r'No\s+Internet\s*:\s*([0-9]+)', text, re.I)
        no_internet = m_no.group(1) if m_no else None
        m_amt = re.search(r'Tagihan\s*:\s*Rp\.?\s*([0-9\.\,]+)', text, re.I)
        amount_text = m_amt.group(1) if m_amt else None
        m_period = re.search(r'bulan\s+([A-Za-z]+(?:\s+\d{4})?)', text, re.I)
        period_text = m_period.group(1) if m_period else None
        if period_text and not re.search(r'\d{4}', period_text):
            m_y = re.search(r'\b(\d{4})\b', text)
            if m_y:
                period_text = f"{period_text} {m_y.group(1)}"
        period_norm, period_month, period_year = self._parse_month_year(period_text or "")
        m_due = re.search(r'sebelum\s+tanggal\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', text, re.I)
        due_iso = None
        if m_due:
            d, mname, y = int(m_due.group(1)), m_due.group(2), int(m_due.group(3))
            mname_en = MONTH_MAP_ID.get(mname.lower(), mname)
            try:
                due_iso = datetime.strptime(f"{d} {mname_en} {y}", "%d %B %Y").date().isoformat()
            except Exception:
                pass
        m_link = re.search(r'(https://payment\.lexxadata\.net\.id/\?id=[\w-]+)', text)
        link_from_text = m_link.group(1) if m_link else None
        return {
            "customer_name": customer_name,
            "no_internet": no_internet,
            "amount_text": amount_text,
            "period_text": period_norm,
            "period_month": period_month,
            "period_year": period_year,
            "due_date_iso": due_iso,
            "payment_link_from_text": link_from_text
        }

    def _payment_link_from_li_or_modal(self, li, soup) -> Tuple[Optional[str], Optional[str]]:
        inp = li.find("input", attrs={"type": "text"})
        if inp and inp.get("value", "").startswith("https://payment.lexxadata.net.id/"):
            modal = self._find_modal_for_li(li, soup)
            ta = modal.select_one('textarea[name="deskripsi_edit"]') if modal else None
            return inp.get("value").strip(), (ta.get_text() if ta else None)
        modal = self._find_modal_for_li(li, soup)
        if modal:
            ta = modal.select_one('textarea[name="deskripsi_edit"]')
            ta_text = ta.get_text() if ta else None
            if ta_text:
                m = re.search(r'(https://payment\.lexxadata\.net\.id/\?id=[\w-]+)', ta_text)
                if m:
                    return m.group(1), ta_text
            return None, ta_text
        return None, None
    
    def parse_tickets(self, html_content: str) -> List[TicketItem]:
        soup = BeautifulSoup(html_content, "html.parser")
        tickets = []

        # Iterate through the table rows
        rows = soup.select("table.table-bordered tbody tr")

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue

            # --- 1. Basic Info ---
            ref_id = cols[0].get_text(strip=True)
            
            # Only include tickets that start with "TN"
            if not ref_id.startswith("TN"):
                continue
                
            date_created = cols[1].get_text(strip=True)

            # --- 2. Modal Extraction ---
            modal = row.find("div", class_="modal")
            
            ticket_description = None
            ticket_action = None

            if modal:
                # Parse Timeline for Description and Action
                timeline_items = modal.select(".track-order-list ul li")
                
                for item in timeline_items:
                    # Get Header (Actor) and Body (Message)
                    h5_tag = item.find("h5")
                    header_text = h5_tag.get_text(strip=True).upper() if h5_tag else ""

                    date_p = item.find("p", class_="text-muted")
                    body_text = ""
                    if date_p:
                        # The content is in the paragraph immediately following the date
                        detail_p = date_p.find_next_sibling("p", class_="text-muted")
                        if detail_p:
                            body_text = detail_p.get_text(strip=True)

                    # --- LOGIC ---

                    # 1. Description: Comes from "OPENED"
                    if "OPENED" in header_text and not ticket_description:
                        ticket_description = body_text

                    # 2. Action: Only capture if closed by TECHNICIAN or NOC
                    # This explicitly ignores "CLOSED BY CS"
                    if "CLOSED BY TECHNICIAN" in header_text or "CLOSED BY NOC" in header_text:
                        ticket_action = body_text

            # Append result
            tickets.append(TicketItem(
                ref_id=ref_id,
                date_created=date_created,
                description=ticket_description or "N/A", 
                action=ticket_action or "Pending/Check Timeline"
            ))

        return tickets

    def get_invoice_data(self, url: str) -> dict:
        try:
            # Added shorter timeout for direct lookups
            res = self.session.get(url, verify=False, timeout=10)
            res.raise_for_status()
        except requests.RequestException as e:
            # Return empty structure on failure so API doesn't crash
            return {
                "paket": None,
                "coordinate": None,
                "user_join": None,
                "mobile": None,
                "invoices": [], 
                "summary": {
                    "this_month": "Error", 
                    "arrears_count": 0, 
                    "last_paid_month": None
                }
            }

        soup = BeautifulSoup(res.text, "html.parser")

        # Helper function to extract profile values (strong -> sibling span pattern)
        def get_profile_value(label_text: str) -> str:
            strong = soup.find('strong', string=lambda t: t and label_text in t)
            if strong:
                value_span = strong.find_next_sibling('span')
                if value_span:
                    return value_span.get_text(strip=True)
            return None

        # Extract profile values
        package_current = get_profile_value("Paket")
        last_paid = get_profile_value("Last Payment")
        user_join = get_profile_value("User Join")
        mobile_raw = get_profile_value("Mobile")
        
        # Normalize mobile to 62 format
        mobile = None
        if mobile_raw:
            if mobile_raw.startswith("0"):
                mobile = "62" + mobile_raw[1:]
            else:
                mobile = mobile_raw
        
        # Extract coordinate from input name="coordinat" with value="lat,lng"
        coord_input = soup.find("input", {"name": "coordinat"})
        if coord_input and coord_input.get("value"):
            coord_value = coord_input.get("value", "").strip()
            # Format: "-8.122402,111.913993"
            if coord_value and "," in coord_value:
                coordinate = coord_value
        
        # Fallback: Try finding latitude/longitude from table rows and combine
        if not coordinate:
            latitude = None
            longitude = None
            for row in soup.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                for i, cell in enumerate(cells):
                    cell_text = cell.get_text(strip=True).lower()
                    if 'lattitude' in cell_text or 'latitude' in cell_text:
                        if i + 1 < len(cells):
                            latitude = cells[i + 1].get_text(strip=True)
                        elif cell.find_next_sibling():
                            latitude = cell.find_next_sibling().get_text(strip=True)
                    elif 'longitude' in cell_text:
                        if i + 1 < len(cells):
                            longitude = cells[i + 1].get_text(strip=True)
                        elif cell.find_next_sibling():
                            longitude = cell.find_next_sibling().get_text(strip=True)
            
            # Try paragraphs if table didn't work
            if not latitude:
                lat_tag = soup.find(lambda tag: tag.name == 'p' and ('lattitude' in tag.get_text().lower() or 'latitude' in tag.get_text().lower()))
                if lat_tag and lat_tag.span:
                    latitude = lat_tag.span.get_text(strip=True)
            
            if not longitude:
                lng_tag = soup.find(lambda tag: tag.name == 'p' and 'longitude' in tag.get_text().lower())
                if lng_tag and lng_tag.span:
                    longitude = lng_tag.span.get_text(strip=True)
            
            # Combine into coordinate if both found
            if latitude and longitude:
                coordinate = f"{latitude},{longitude}"

        invoices = []
        timeline_items = soup.select("ul.list-unstyled.timeline-sm > li.timeline-sm-item")
        for item in timeline_items:
            status_tag = item.select_one("span.timeline-sm-date span.badge")
            status = status_tag.get_text(strip=True) if status_tag else None
            package_tag = item.select_one("h5")
            package_name = package_tag.get_text(strip=True) if package_tag else None
            period_tag = package_tag.find_next_sibling("p") if package_tag else None
            period = period_tag.get_text(strip=True) if period_tag else None
            link_tag = item.select_one("input[value^='https://payment.lexxadata.net.id']")
            payment_link = link_tag['value'] if link_tag else None
            
            description = None
            bc_wa_button = item.select_one("button[data-target*='modaleditt']")
            if bc_wa_button and bc_wa_button.get('data-target'):
                modal_id = bc_wa_button['data-target']
                modal = soup.select_one(modal_id)
                if modal:
                    textarea = modal.select_one('textarea[name="deskripsi_edit"]')
                    if textarea:
                        description = textarea.get_text(strip=True)

            period_norm, month, year = self._parse_month_year(period or "")
            
            invoices.append({
                "status": status,
                "package": package_name,
                "period": period,
                "month": month,
                "year": year,
                "payment_link": payment_link,
                "amount": None,
                "description": description,
                "desc_parsed": {}
            })

        now = datetime.now()
        this_month_invoice = next((inv for inv in invoices if inv.get("year") == now.year and inv.get("month") == now.month), None)
        arrears_count = sum(1 for inv in invoices
                            if inv.get("status") == "Unpaid"
                            and inv.get("year") is not None and inv.get("month") is not None
                            and (inv["year"], inv["month"]) < (now.year, now.month))

        return {
            "paket": package_current,
            "coordinate": coordinate,
            "user_join": user_join,
            "mobile": mobile,
            "invoices": invoices,
            "summary": {
                "this_month": this_month_invoice.get("status") if this_month_invoice else None,
                "arrears_count": arrears_count,
                "last_paid_month": last_paid
            }
        }

    def get_customer_details(self, customer_id: str) -> dict:
        url = settings.DETAIL_URL_BILLING.format(id=customer_id)

        try:
            res = self.session.get(url, verify=False, timeout=15)
            res.raise_for_status()
        except requests.RequestException as e:
            print(f"Failed to fetch customer details: {e}")
            return None

        soup = BeautifulSoup(res.text, "html.parser")

        # --- A. Basic Profile Info ---
        # Name is in the h4 tag inside the profile card [cite: 64]
        # Address is in the paragraph immediately following the name [cite: 64]
        profile_box = soup.select_one("div.card-box.text-center")
        name = "N/A"
        address = "N/A"
        
        if profile_box:
            name_tag = profile_box.find("h4", class_="mb-0")
            if name_tag:
                name = name_tag.get_text(strip=True)
            
            addr_tag = profile_box.find("p", class_="text-muted")
            if addr_tag:
                address = addr_tag.get_text(strip=True)

        # --- B. Key-Value Profile Details ---
        # These are stored in <p> tags with <strong> labels inside div.text-left.mt-3 [cite: 67-69]
        def get_profile_value(label_text):
            # Find the strong tag containing the label
            label = soup.find("strong", string=lambda t: t and label_text in t)
            if label:
                # The value is in the next <span> sibling [cite: 67-69]
                value_span = label.find_next_sibling("span")
                if value_span:
                    return value_span.get_text(strip=True)
            return None

        user_join = get_profile_value("User Join")
        # 'No Internet' in the HTML maps to 'user_pppoe' or 'id' in your model [cite: 68]
        user_pppoe = get_profile_value("No Internet") 
        mobile = get_profile_value("Mobile")
        package = get_profile_value("Paket")
        last_payment = get_profile_value("Last Payment")

        # --- C. Coordinate ---
        # Explicitly stored in <input name="coordinat"> in the settings tab 
        coord_input = soup.find("input", {"name": "coordinat"})
        coordinate = coord_input.get("value", "").strip() if coord_input else None
        wa_link = BillingScraper._parser_whatsapp_url(mobile)
        maps_link = BillingScraper._parser_maps_url(coordinate)

        # --- D. Detail URL (Payment Link) ---
        # The link is hidden inside the textarea of the "BC WA" modal for the LATEST invoice 
        detail_url = None
        invoices = None
        invoice_links = []  # Collect ALL payment links
        
        # Get ALL timeline items (invoices)
        all_invoice_items = soup.select("ul.timeline-sm li.timeline-sm-item")
        
        for idx, invoice_item in enumerate(all_invoice_items):
            # Find the "BC WA" button to get the target modal ID
            wa_button = invoice_item.select_one("button[data-target^='#modaleditt']")
            
            if wa_button:
                modal_id = wa_button.get("data-target")
                modal = soup.select_one(modal_id)
                
                if modal:
                    textarea = modal.find("textarea", {"name": "deskripsi_edit"})
                    if textarea:
                        ta_text = textarea.get_text()
                        # Find payment link in textarea
                        match = re.search(r'(https://payment\.lexxadata\.net\.id/\?id=[\w-]+)', ta_text)
                        if match:
                            link = match.group(1)
                            invoice_links.append(link)
                            # First one is the latest (detail_url)
                            if idx == 0:
                                detail_url = link
                                invoices = ta_text
            
            # Also check for direct input with payment link
            link_input = invoice_item.select_one("input[value^='https://payment.lexxadata.net.id']")
            if link_input:
                link = link_input.get("value")
                if link and link not in invoice_links:
                    invoice_links.append(link)
        
        tickets = self.parse_tickets(res.text)

        # Return the populated model
        return Customer(
            id=customer_id,
            name=name,
            address=address,
            user_pppoe=user_pppoe,
            package=package,
            coordinate=coordinate,
            user_join=user_join,
            mobile=mobile,
            last_payment=last_payment,
            detail_url=detail_url,  # None if no payment link found
            invoices=invoices,
            wa_link=wa_link,
            maps_link=maps_link,
            tickets=tickets
        )


class NOCScrapper:
    def __init__(self):
        self.session = requests.Session()
        self._login()

    def _save_cookies(self):
        with open("noc_session.pkl", "wb") as f:
            pickle.dump(self.session.cookies, f)

    def _load_cookies(self) -> bool:
        if os.path.exists("noc_session.pkl"):
            with open("noc_session.pkl", "rb") as f:
                self.session.cookies.update(pickle.load(f))
            return True
        return False

    def _is_logged_in(self) -> bool:
        try:
            r = self.session.get(settings.LOGIN_URL_BILLING, verify=False, allow_redirects=False, timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _login(self):
        if self._load_cookies() and self._is_logged_in():
            return

        payload = {"username": settings.NMS_USERNAME, "password": settings.NMS_PASSWORD}
        try:
            r = self.session.post(settings.LOGIN_URL_BILLING, data=payload, verify=False, timeout=10)
            if r.status_code not in (200, 302):
                raise ConnectionError(f"Login failed with status code {r.status_code}")
            
            self._save_cookies()
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to connect to the login page: {e}")
    
    def _get_data_psb(self) -> List[Dict]:
        url_psb = settings.DATA_PSB_URL
        res = None

        for attempt in range(2):
            try:
                res = self.session.get(url_psb, verify=False, timeout=15)
                res.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 0:
                    self._login()
                else:
                    return []
        
        if not res:
            return []

        soup = BeautifulSoup(res.text, "html.parser")
        table_rows = soup.select("#tickets-note tbody tr")
        
        if not table_rows:
            return []

        data_psb = []
        for row in table_rows:
            cols = [c.get_text(strip=True) for c in row.select("td")]

            if len(cols) < 5:
                continue

            details_link = row.select_one('a[data-target]')
            framed_pool = None
            if details_link:
                modal_id = details_link.get("data-target", "").strip("#")
                if modal_id:
                    modal = soup.select_one(f"div.modal#{modal_id}")
                    if modal:
                        for p in modal.select("p.mb-0"):
                            text = p.get_text(strip=True)
                            if "framed-pool" in text.lower():
                                match = re.search(r"(\d+M)", text)
                                if match:
                                    framed_pool = match.group(1)
                                break
            
            data_psb.append({
                "name": cols[0],
                "address": cols[1],
                "user_pppoe": cols[3],
                "pppoe_password": cols[4],
                "paket": framed_pool
            })
            
        return data_psb