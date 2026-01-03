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

from core.config import settings

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
            details_link_tag = cols[4].find("a", href=re.compile(r"deusr&id=(\d+)"))
            if not all([name_tag, address_tag, details_link_tag]) or len(pppoe_tags) < 2:
                continue
            match = re.search(r"id=(\d+)", details_link_tag['href'])
            if not match:
                continue
            
            collected_data.append({
                "id": match.group(1),
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

    def get_invoice_data(self, url: str) -> dict:
        try:
            # Added shorter timeout for direct lookups
            res = self.session.get(url, verify=False, timeout=10)
            res.raise_for_status()
        except requests.RequestException as e:
            # Return empty structure on failure so API doesn't crash
            return {
                "paket": None, 
                "invoices": [], 
                "summary": {
                    "this_month": "Error", 
                    "arrears_count": 0, 
                    "last_paid_month": None
                }
            }

        soup = BeautifulSoup(res.text, "html.parser")

        package_current = None
        last_paid = None
        paket_p_tag = soup.find('p', string=lambda text: text and 'Paket :' in text)
        if paket_p_tag and paket_p_tag.span:
            package_current = paket_p_tag.span.get_text(strip=True)
        last_payment_p_tag = soup.find('p', string=lambda text: text and 'Last Payment :' in text)
        if last_payment_p_tag and last_payment_p_tag.span:
            last_paid = last_payment_p_tag.span.get_text(strip=True)

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
            "invoices": invoices,
            "summary": {
                "this_month": this_month_invoice.get("status") if this_month_invoice else None,
                "arrears_count": arrears_count,
                "last_paid_month": last_paid
            }
        }


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
            r = self.session.get(settings.LOGIN_URL, verify=False, allow_redirects=False, timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _login(self):
        if self._load_cookies() and self._is_logged_in():
            return

        payload = {"username": settings.NMS_USERNAME, "password": settings.NMS_PASSWORD}
        try:
            r = self.session.post(settings.LOGIN_URL, data=payload, verify=False, timeout=10)
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