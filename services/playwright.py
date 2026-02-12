import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from core.config import settings
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from concurrent.futures import ThreadPoolExecutor
import asyncio
import time
import requests
from bs4 import BeautifulSoup
from api.v1.endpoints.ocr import _process_image_ocr as ocr
from schemas.customers_scrapper import (
    Customer,
    TicketItem,
    InvoiceItem,
    BillingSummary,
    CustomerwithInvoices,
)

# Month mapping for Indonesian to English
MONTH_MAP_ID = {
    "januari": "January",
    "februari": "February",
    "maret": "March",
    "april": "April",
    "mei": "May",
    "juni": "June",
    "juli": "July",
    "agustus": "August",
    "september": "September",
    "oktober": "October",
    "november": "November",
    "desember": "December",
}

# Configure logging to show messages
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)



LOGIN_URL = settings.LOGIN_URL_BILLING
DASHBOARD_URL_GLOB = "**/billing2/**"  # pattern for dashboard after login
INVOICES_URL = settings.DETAIL_URL_BILLING
TICKET_URL = settings.TICKET_NOC_URL
DATA_PSB_URL = settings.DATA_PSB_URL

username_cs = settings.NMS_USERNAME_BILING
password_cs = settings.NMS_PASSWORD_BILING

username_noc = settings.NMS_USERNAME
password_noc = settings.NMS_PASSWORD


# Session storage paths
SESSION_DIR = Path(__file__).parent / "sessions"
SESSION_CS_FILE = SESSION_DIR / "session_cs.json"
SESSION_NOC_FILE = SESSION_DIR / "session_noc.json"


def _evaluate_math_captcha(text: str) -> Optional[int]:
    """
    Evaluate if CAPTCHA text is a math expression and return the answer.
    """
    try:
        # Remove all whitespace for easier parsing
        clean = text.replace(" ", "").replace("=", "").replace("?", "")
        
        # Check if it matches a simple math pattern: number operator number
        # Supports: +, -, *, /, x (as multiplication)
        math_pattern = r'^(\d+)\s*([+\-*/x×])\s*(\d+)$'
        match = re.search(math_pattern, clean, re.IGNORECASE)
        
        if match:
            num1 = int(match.group(1))
            operator = match.group(2).lower()
            num2 = int(match.group(3))
            
            # Map operators
            if operator == '+':
                result = num1 + num2
            elif operator == '-':
                result = num1 - num2
            elif operator in ['*', 'x', '×']:
                result = num1 * num2
            elif operator == '/':
                result = num1 // num2  # Integer division
            else:
                return None
            
            return int(result)
        
        return None
        
    except Exception as e:
        print(f"⚠️ Math evaluation failed: {e}")
        return None

# Thread pool for running sync playwright in async context
_executor = ThreadPoolExecutor(max_workers=2)


def run_sync(func, *args, **kwargs):
    """Run a sync function in thread pool, return awaitable."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


class CustomerService:
    """Sync Playwright service for customer operations.
    Use run_sync() wrapper when calling from async FastAPI endpoints.
    """

    def __init__(self, username: str = None, password: str = None):
        self.username = username or username_cs
        self.password = password or password_cs
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.session_file = SESSION_CS_FILE
        self._logged_in = False

    def start(self, headless: bool = True):
        """Start browser (sync). Call this first."""
        # Ensure session directory exists
        SESSION_DIR.mkdir(exist_ok=True)

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)

        # Try to load existing session
        if self.session_file.exists():
            logging.info(f"Loading session from {self.session_file}")
            self.context = self.browser.new_context(
                storage_state=str(self.session_file)
            )
        else:
            logging.info("No existing session found, creating new context")
            self.context = self.browser.new_context()

        self.page = self.context.new_page()

    def save_session(self):
        """Save current session (cookies, localStorage) to file."""
        if self.context:
            self.context.storage_state(path=str(self.session_file))
            logging.info(f"Session saved to {self.session_file}")

    def close(self, save: bool = True):
        """Close browser and cleanup."""
        if save and self.context:
            self.save_session()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def is_logged_in(self) -> bool:
        """Check if already logged in by trying to access a protected page."""
        if self._logged_in:
            return True
        try:
            # Try accessing a protected page directly
            self.page.goto(LOGIN_URL.replace('login', ''), wait_until="domcontentloaded", timeout=5000)
            
            # If we're NOT on the login page, session is valid
            current_url = self.page.url.lower()
            if "login" not in current_url and "billing2" in current_url:
                logging.info("Already logged in (session restored)")
                self._logged_in = True
                return True
            return False
        except Exception:
            return False

    def login(self) -> bool:
        """Login to the billing system."""
        if not self.page:
            raise RuntimeError("Call start() first")

        # Skip if already logged in this session
        if self._logged_in:
            return True

        # Check if saved session is still valid
        if self.is_logged_in():
            return True

        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logging.info("Going to Login Page")

        max_attempts = 3
        for attempt in range(max_attempts):
            logging.info(f"Login attempt {attempt + 1}/{max_attempts}")
            
            # Check for CAPTCHA image
            captcha_img = self.page.locator('img[src*="captcha.php"]').first
            captcha_input = self.page.locator('input[name="captcha"]').first
            
            captcha_text = None
            
            if captcha_img.count() > 0 and captcha_img.is_visible():
                logging.info("CAPTCHA detected, solving...")
                try:
                    # Take screenshot of the CAPTCHA element
                    captcha_bytes = captcha_img.screenshot()
                    
                    # Solve using OCR
                    ocr_text = ocr(captcha_bytes)
                    logging.info(f"OCR Result: '{ocr_text}'")
                    
                    if ocr_text:
                        # Check for math expression
                        math_answer = _evaluate_math_captcha(ocr_text)
                        
                        if math_answer is not None:
                            captcha_text = str(math_answer)
                            logging.info(f"Math solution: {captcha_text}")
                        else:
                            captcha_text = ocr_text.strip()
                            logging.info(f"CAPTCHA text: {captcha_text}")
                            
                        # Fill CAPTCHA field
                        if captcha_input.count() > 0:
                            captcha_input.fill(captcha_text)
                except Exception as e:
                    logging.error(f"Error solving CAPTCHA: {e}")

            self.page.get_by_placeholder("Username").fill(self.username)
            logging.info("Username filled")
            self.page.get_by_placeholder("Password").fill(self.password)
            logging.info("Password filled")
            self.page.get_by_role("button", name="Sign In").click()

            try:
                self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=5000)
                # Save session after successful login
                self.save_session()
                self._logged_in = True
                logging.info("Login successful")
                return True
            except PWTimeoutError:
                err = self.page.get_by_text("Invalid username or password")
                if err.is_visible():
                    raise ValueError("Invalid username or password")
                
                # If we are still on login page, it's likely a temporary failure or CAPTCHA mismatch
                if "login" in self.page.url.lower():
                    logging.warning("Login failed (likely CAPTCHA), retrying...")
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                        # Reload page to get new CAPTCHA
                        self.page.reload() 
                        continue
                    else:
                        logging.error("Max login attempts reached")
        
        raise ValueError("Login failed after multiple attempts")

    def search_user(self, query: str):
        """Search for customers by name or number.
        Note: Caller must ensure login() has been called first.
        """

        field = self.page.get_by_placeholder("Name Or No Internet")
        field.fill(query)
        field.press("Enter")

        # Wait for search results to load
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_text(query, exact=False).first.wait_for(timeout=10_000)

    def get_invoices(self, query: str = None, customer_id: str = None):
        """Get invoice data for a customer.

        Args:
            query: Internet number to search for
            customer_id: Customer ID to search for
        """
        ok = self.login()
        if not ok:
            return None

        # Use customer_id or query as search term
        search_term = customer_id or query
        if not search_term:
            logging.error("Either query or customer_id must be provided")
            return None

        # Search for the user first
        logging.info(f"Searching for: {search_term}")
        self.search_user(search_term)

        # Get the Detail User link href and navigate to it
        # (the link is inside a hidden dropdown menu, so we extract the href directly)
        detail_link = self.page.locator("a.dropdown-item[href*='deusr']").first
        if detail_link.count() > 0:
            href = detail_link.get_attribute("href")
            if href:
                # Build full URL from relative href
                base_url = self.page.url.rsplit("/", 1)[0]
                detail_url = f"{base_url}/{href}" if not href.startswith("http") else href
                logging.info(f"Navigating to Detail User: {detail_url}")
                self.page.goto(detail_url, wait_until="networkidle")
                logging.info("Navigated to Detail User page")
            else:
                logging.error("Detail User link has no href")
                return None
        else:
            logging.error(f"Could not find Detail User link for: {search_term}")
            return None

        # Helper to extract profile values
        def get_profile_value(label_text: str) -> str:
            try:
                label = self.page.locator(f"strong:has-text('{label_text}')").first
                if label.count() > 0:
                    value_span = label.locator("xpath=following-sibling::span").first
                    if value_span.count() > 0:
                        return value_span.inner_text().strip()
            except:
                pass
            return ""

        # Extract profile data
        data = {
            "user_join": get_profile_value("User Join"),
            "no_internet": get_profile_value("No Internet"),
            "mobile": get_profile_value("Mobile"),
            "nik": get_profile_value("NIK"),
            "paket": get_profile_value("Paket"),
            "last_payment": get_profile_value("Last Payment"),
            "uptime": get_profile_value("Uptime"),
            "bw_usage": get_profile_value("Bw Usage Up/Down"),
            "sn_modem": get_profile_value("SN Modem"),
        }

        # Get the invoice description from textarea
        textarea = self.page.locator("textarea[name='deskripsi_edit']").first
        invoices = ""
        if textarea.count() > 0:
            invoices = textarea.input_value()

        data["invoices"] = invoices

        logging.info(f"Invoice data retrieved for: {query}")
        return data

    def create_ticket(
        self, query: str, description: str, priority: str = "LOW", jenis: str = "FREE"
    ):
        """Create a ticket for a customer using pure HTTP (no browser).

        Args:
            query: Internet number or name to search for
            description: Ticket description
            priority: LOW, MEDIUM, or HIGH
            jenis: FREE or CHARGED

        Returns:
            True on success, False/None on failure
        """
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

        # Step 1: Login via HTTP POST
        login_payload = {"username": self.username, "password": self.password}
        try:
            res = session.post(
                settings.LOGIN_URL,
                data=login_payload,
                verify=False,
                timeout=10,
                allow_redirects=True,
            )
            if res.status_code not in (200, 302) or "login" in res.url.lower():
                logging.error("HTTP login failed")
                return None
            logging.info("HTTP login successful")
        except requests.RequestException as e:
            logging.error(f"Login request failed: {e}")
            return None

        # Step 2: Search to get HTML with pre-populated modal form
        search_payload = {"type_cari": query, "cari_tagihan": ""}
        try:
            res = session.post(
                settings.BILLING_MODULE_BASE,
                data=search_payload,
                verify=False,
                timeout=15,
                allow_redirects=True,
            )
            res.raise_for_status()
        except requests.RequestException as e:
            logging.error(f"Search request failed: {e}")
            return None

        # Step 3: Parse modal form from search results
        soup = BeautifulSoup(res.text, "html.parser")
        modal = soup.select_one("div[id^='create_tiga_modal']")
        if not modal:
            logging.error(f"No ticket modal found for query: {query}")
            return None

        # Step 4: Extract all form fields from modal
        form = modal.find("form")
        if not form:
            logging.error("Ticket form not found in modal")
            return None

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        # Override with user-provided values
        payload["priority"] = priority.upper()
        payload["jenis_ticket"] = jenis.upper()
        payload["deskripsi"] = description
        payload["create_ticket_gangguan"] = ""  # Submit button name

        logging.info(f"Submitting ticket for query: {query}")

        # Step 5: Submit the form
        try:
            res = session.post(
                settings.BILLING_MODULE_BASE,
                data=payload,
                verify=False,
                timeout=15,
                allow_redirects=True,
            )
            res.raise_for_status()

            if "berhasil" in res.text.lower() or res.status_code == 200:
                logging.info(f"Ticket created successfully for query: {query}")
                return True
            else:
                logging.error("Ticket creation may have failed")
                return False

        except requests.RequestException as e:
            logging.error(f"Submit request failed: {e}")
            return None

    @staticmethod
    def _parse_month_year(
        text: str,
    ) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """Parse month and year from text like 'Januari 2025'."""
        if not text:
            return None, None, None
        t = text.strip()
        low = t.lower()
        for indo, eng in MONTH_MAP_ID.items():
            if indo in low:
                t = low.replace(indo, eng).title()
                break
        m = re.search(r"([A-Za-z]+)\s+(\d{4})", t)
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
        """Generate WhatsApp URL from mobile number."""
        if not mobile:
            return None
        clean_number = mobile.strip()
        if clean_number == "0":
            return None
        return f"https://wa.me/{clean_number}"

    @staticmethod
    def _parser_maps_url(coordinate: str) -> Optional[str]:
        """Generate Google Maps URL from coordinates."""
        if not coordinate:
            return None
        clean_coordinate = coordinate.strip()
        if clean_coordinate == "0":
            return None
        return f"https://www.google.com/maps?q={clean_coordinate}"


class NOC:
    """Sync Playwright service for NOC operations."""

    def __init__(self, username: str = None, password: str = None):
        self.username = username or username_noc
        self.password = password or password_noc
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.session_file = SESSION_NOC_FILE
        self._logged_in = False

    def start(self, headless: bool = True):
        SESSION_DIR.mkdir(exist_ok=True)

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)

        if self.session_file.exists():
            logging.info(f"Loading NOC session from {self.session_file}")
            self.context = self.browser.new_context(
                storage_state=str(self.session_file)
            )
        else:
            logging.info("No existing NOC session found, creating new context")
            self.context = self.browser.new_context()

        self.page = self.context.new_page()

    def save_session(self):
        if self.context:
            self.context.storage_state(path=str(self.session_file))
            logging.info(f"NOC session saved to {self.session_file}")

    def close(self, save: bool = True):
        if save and self.context:
            self.save_session()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def is_logged_in(self) -> bool:
        if self._logged_in:
            return True
        try:
            # Check NOC session by going to PSB data page
            self.page.goto(DATA_PSB_URL, wait_until="domcontentloaded", timeout=5000)
            
            current_url = self.page.url.lower()
            
            # Check for login redirection
            if "login" in current_url:
                return False

            # Check for specific element that confirms we are logged in
            # The PSB table should be visible if we are at DATA_PSB_URL and logged in
            try:
                self.page.wait_for_selector("#tickets-note", timeout=3000)
                logging.info("Already logged in (NOC session restored)")
                self._logged_in = True
                return True
            except:
                # If table not found, maybe we are on dashboard but not PSB page?
                # Check for common dashboard element
                if self.page.locator(".navbar-custom").count() > 0:
                     logging.info("Already logged in (NOC session restored)")
                     self._logged_in = True
                     return True
            
            return False
        except Exception:
            return False

    def login(self) -> bool:
        if not self.page:
            raise RuntimeError("Call start() first")
        
        # Skip if already logged in this session
        if self._logged_in:
            return True

        # Check if saved session is still valid
        if self.is_logged_in():
            return True

        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logging.info("NOC: Going to Login Page")

        max_attempts = 3
        for attempt in range(max_attempts):
            logging.info(f"NOC: Login attempt {attempt + 1}/{max_attempts}")
            
            # Check for CAPTCHA image
            captcha_img = self.page.locator('img[src*="captcha.php"]').first
            captcha_input = self.page.locator('input[name="captcha"]').first
            
            captcha_text = None
            
            if captcha_img.count() > 0 and captcha_img.is_visible():
                logging.info("NOC: CAPTCHA detected, solving...")
                try:
                    # Take screenshot of the CAPTCHA element
                    captcha_bytes = captcha_img.screenshot()
                    
                    # Solve using OCR
                    ocr_text = ocr(captcha_bytes)
                    logging.info(f"NOC: OCR Result: '{ocr_text}'")
                    
                    if ocr_text:
                        # Check for math expression
                        math_answer = _evaluate_math_captcha(ocr_text)
                        
                        if math_answer is not None:
                            captcha_text = str(math_answer)
                            logging.info(f"NOC: Math solution: {captcha_text}")
                        else:
                            captcha_text = ocr_text.strip()
                            logging.info(f"NOC: CAPTCHA text: {captcha_text}")
                            
                        # Fill CAPTCHA field
                        if captcha_input.count() > 0:
                            captcha_input.fill(captcha_text)
                except Exception as e:
                    logging.error(f"NOC: Error solving CAPTCHA: {e}")

            # Fill credentials
            self.page.get_by_placeholder("Username").fill(self.username)
            logging.info("NOC: Username filled")
            self.page.get_by_placeholder("Password").fill(self.password)
            logging.info("NOC: Password filled")
            
            # Click sign in
            self.page.get_by_role("button", name="Sign In").click()

            try:
                # Wait for either success (dashboard) or failure
                self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=5000)
                self.save_session()
                self._logged_in = True
                logging.info("NOC: Login successful")
                return True
                
            except PWTimeoutError:
                # Check for specific error messages
                err_msg = self.page.locator("text=Invalid username or password")
                if err_msg.count() > 0 and err_msg.is_visible():
                    raise ValueError("Invalid username or password")
                
                # If we are still on login page, it's likely a temporary failure or CAPTCHA mismatch
                if "login" in self.page.url.lower():
                    logging.warning("NOC: Login failed (likely CAPTCHA), retrying...")
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                        # Reload page to get new CAPTCHA
                        self.page.reload() 
                        continue
                    else:
                        logging.error("NOC: Max login attempts reached")
                        
        raise ValueError("Login failed after multiple attempts")

    def process_ticket(self, nama_pelanggan: str, action: str):
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        # TODO: Implement ticket processing logic
        pass

    def get_data_psb(self) -> list:
        """Get PSB (Pemasangan Baru) data from the NOC dashboard.
        
        Returns:
            List of dicts with keys: name, address, username, password, package
        """
        self.login()
        self.page.goto(DATA_PSB_URL, wait_until="networkidle")
        logging.info("Navigated to PSB data page")

        # Wait for the table to load
        table = self.page.locator("#tickets-note")
        table.wait_for(state="visible", timeout=10_000)

        results = []
        rows = table.locator("tbody tr")
        row_count = rows.count()
        logging.info(f"Found {row_count} PSB rows")

        for i in range(row_count):
            row = rows.nth(i)
            cells = row.locator("td")

            try:
                name = cells.nth(0).inner_text().strip()
                address = cells.nth(1).inner_text().strip()
                username = cells.nth(3).inner_text().strip()
                password = cells.nth(4).inner_text().strip()

                # Open the detail modal to get Framed-Pool (package)
                package = ""
                try:
                    # Click the dropdown toggle in the Action column
                    dropdown_toggle = row.locator("a.dropdown-toggle").first
                    dropdown_toggle.click()
                    
                    # Click the Details link
                    details_link = row.locator("a.dropdown-item:has-text('Details')").first
                    details_link.wait_for(state="visible", timeout=3000)
                    details_link.click()
                    
                    # Wait for modal to appear
                    modal = self.page.locator(".modal.show .modal-body")
                    modal.wait_for(state="visible", timeout=5000)
                    
                    # Extract Framed-Pool value from modal text
                    modal_text = modal.inner_text()
                    for line in modal_text.split("\n"):
                        if "Framed-Pool" in line:
                            # Parse: "Framed-Pool   =   CIGNAL 25M (RP 125.000)"
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                package = parts[1].strip()
                            break
                    
                    logging.info(f"Row {i} package: {package}")
                    
                    # Close the modal
                    close_btn = self.page.locator(".modal.show button[data-dismiss='modal']").first
                    if close_btn.count() > 0:
                        close_btn.click()
                        self.page.wait_for_timeout(500)
                        
                except Exception as e:
                    logging.warning(f"Failed to get package for row {i}: {e}")

                results.append({
                    "name": name,
                    "address": address,
                    "username": username,
                    "password": password,
                    "package": package,
                })
            except Exception as e:
                logging.warning(f"Failed to parse PSB row {i}: {e}")
                continue

        logging.info(f"Extracted {len(results)} PSB records")
        return results


# Convenience functions for running sync methods from async endpoints
def search_customer_sync(query: str, headless: bool = True):
    """Run customer search synchronously (for use with run_sync)."""
    service = CustomerService()
    try:
        service.start(headless=headless)
        return service.search_user(query)
    finally:
        service.close()


def get_customer_with_invoices_sync(query: str, headless: bool = True):
    """Search and get invoices for single customer (for use with run_sync)."""
    service = CustomerService()
    try:
        service.start(headless=headless)
        results = service.search_user(query)
        if not results:
            return None, None

        if len(results) == 1:
            invoices = service.get_invoices(results[0]["id"])
            return results, invoices

        return results, None
    finally:
        service.close()


def get_customer_details_sync(
    customer_id: str, headless: bool = True
) -> Optional[Customer]:
    """Get comprehensive customer details (for use with run_sync)."""
    service = CustomerService()
    try:
        service.start(headless=headless)
        return service.get_invoices(customer_id=customer_id)
    finally:
        service.close()


def get_invoice_data_sync(
    customer_id: str, headless: bool = True
) -> Optional[CustomerwithInvoices]:
    """Get detailed invoice data for customer (for use with run_sync)."""
    service = CustomerService()
    try:
        service.start(headless=headless)
        return service.get_invoices(customer_id=customer_id)
    finally:
        service.close()


if __name__ == "__main__":
    # Test sync version
    service = NOC()
    try:
        service.start(headless=False)

        # Fast method: use encoded customer ID directly
        # Example ID from the detail URL
        print("=== Testing FAST method (direct customer_id) ===")
        psb_data = service.get_data_psb()
        print(psb_data)
        # Slow method: search by internet number
        # print("=== Testing SLOW method (search query) ===")
        # invoices = service.get_invoices(query="10009124")
        # print("Invoice Data:", invoices)
    finally:
        service.close()
