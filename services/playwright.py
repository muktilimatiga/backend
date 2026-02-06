import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from core.config import settings
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from concurrent.futures import ThreadPoolExecutor
import asyncio
from schemas.customers_scrapper import Customer, TicketItem, InvoiceItem, BillingSummary, CustomerwithInvoices

# Month mapping for Indonesian to English
MONTH_MAP_ID = {
    "januari": "January", "februari": "February", "maret": "March", "april": "April",
    "mei": "May", "juni": "June", "juli": "July", "agustus": "August",
    "september": "September", "oktober": "October", "november": "November",
    "desember": "December"
}

# Configure logging to show messages
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

LOGIN_URL = settings.LOGIN_URL
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

    def start(self, headless: bool = True):
        """Start browser (sync). Call this first."""
        # Ensure session directory exists
        SESSION_DIR.mkdir(exist_ok=True)
        
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)
        
        # Try to load existing session
        if self.session_file.exists():
            logging.info(f"Loading session from {self.session_file}")
            self.context = self.browser.new_context(storage_state=str(self.session_file))
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
        """Check if already logged in by navigating to dashboard."""
        try:
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=3000)
            logging.info("Already logged in (session restored)")
            return True
        except PWTimeoutError:
            return False

    def login(self) -> bool:
        """Login to the billing system."""
        if not self.page:
            raise RuntimeError("Call start() first")
        
        # Check if already logged in from saved session
        if self.is_logged_in():
            return True

        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logging.info("Going to Login Page")

        self.page.get_by_placeholder("Username").fill(self.username)
        logging.info("Username filled")
        self.page.get_by_placeholder("Password").fill(self.password)
        logging.info("Password filled")
        self.page.get_by_role("button", name="Sign In").click()

        try:
            self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=15_000)
            # Save session after successful login
            self.save_session()
            return True
        except PWTimeoutError:
            err = self.page.get_by_text("Invalid username or password")
            if err.is_visible():
                raise ValueError("Invalid username or password")
            raise

    def search_user(self, query: str):
        """Search for customers by name or number."""
        ok = self.login()
        if not ok:
            return None

        field = self.page.get_by_placeholder("Name Or No Internet")
        field.fill(query)
        field.press("Enter")

        # Wait for search results to load
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_text(query, exact=False).first.wait_for(timeout=10_000)
    
    def get_invoices(self, query: str = None, customer_id: str = None):
        """Get invoice data for a customer.
        
        Args:
            query: Internet number to search for (slower - requires search first)
            customer_id: Encoded customer ID for direct URL access (faster)
        """
        ok = self.login()
        if not ok:
            return None
        
        if customer_id:
            # Direct navigation using customer ID (fast)
            detail_url = INVOICES_URL.format(id=customer_id)
            logging.info(f"Going directly to: {detail_url}")
            self.page.goto(detail_url, wait_until="networkidle")
        elif query:
            # Search first, then click detail (slower)
            self.search_user(query)
            
            # Click on Detail User dropdown link to go to invoices page
            detail_link = self.page.locator("a.dropdown-item[href*='deusr']").first
            if detail_link.count() > 0:
                detail_link.click()
                self.page.wait_for_load_state("networkidle")
            else:
                logging.error(f"Could not find Detail User link for query: {query}")
                return None
        else:
            logging.error("Either query or customer_id must be provided")
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
    
    def create_ticket(self, query: str, description: str, priority: str = "LOW", jenis: str = "FREE"):
        """Create a ticket for a customer.
        
        Args:
            query: Internet number or name to search for
            description: Ticket description
            priority: LOW, MEDIUM, or HIGH
            jenis: FREE or CHARGED
        """
        ok = self.login()
        if not ok:
            return None
            
        # Search for the user
        self.search_user(query)
        
        # Find the first row's action dropdown
        action_dropdown = self.page.locator("a.dropdown-toggle.table-action-btn").first
        if action_dropdown.count() == 0:
            logging.error(f"Could not find action dropdown for query: {query}")
            return None
            
        action_dropdown.click()
        
        # Click on "Ticket Gangguan" link and get modal ID dynamically
        ticket_link = self.page.locator("a.dropdown-item:has-text('Ticket Gangguan')").first
        if ticket_link.count() == 0:
            logging.error(f"Could not find Ticket Gangguan link for query: {query}")
            return None
        
        # Get the modal ID from data-target attribute
        data_target = ticket_link.get_attribute("data-target")
        if not data_target:
            logging.error("Ticket Gangguan link has no data-target")
            return None
        
        modal_id = data_target.lstrip("#")
        ticket_link.click()
        logging.info(f"Opened Ticket Gangguan modal: {modal_id}")
        
        # Wait for modal to be visible using the dynamic ID
        modal = self.page.locator(f"[id='{modal_id}']")
        modal.wait_for(state="visible", timeout=5000)
        
        # Fill the form
        modal.locator("select[name='priority']").select_option(priority.upper())
        logging.info(f"Selected priority: {priority}")
        
        modal.locator("select[name='jenis_ticket']").select_option(jenis.upper())
        logging.info(f"Selected type: {jenis}")
        
        modal.locator("textarea[name='deskripsi']").fill(description)
        logging.info(f"Filled description: {description}")
        
        modal.locator("button[name='create_ticket_gangguan']").click()
        logging.info("Clicked Save button")
        
        self.page.wait_for_load_state("networkidle")
        
        logging.info(f"Ticket created successfully for query: {query}")
        return True

    @staticmethod
    def _parse_month_year(text: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """Parse month and year from text like 'Januari 2025'."""
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

    def start(self, headless: bool = True):
        SESSION_DIR.mkdir(exist_ok=True)
        
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)
        
        if self.session_file.exists():
            logging.info(f"Loading NOC session from {self.session_file}")
            self.context = self.browser.new_context(storage_state=str(self.session_file))
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
        try:
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=3000)
            logging.info("Already logged in (NOC session restored)")
            return True
        except PWTimeoutError:
            return False

    def login(self) -> bool:
        if not self.page:
            raise RuntimeError("Call start() first")
        
        if self.is_logged_in():
            return True

        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logging.info("NOC: Going to Login Page")

        self.page.get_by_placeholder("Username").fill(self.username)
        logging.info("NOC: Username filled")
        self.page.get_by_placeholder("Password").fill(self.password)
        logging.info("NOC: Password filled")
        self.page.get_by_role("button", name="Sign In").click()

        try:
            self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=15_000)
            self.save_session()
            return True
        except PWTimeoutError:
            err = self.page.get_by_text("Invalid username or password")
            if err.is_visible():
                raise ValueError("Invalid username or password")
            raise
    
    def process_ticket(self, nama_pelanggan: str, action: str):
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        # TODO: Implement ticket processing logic
        pass
    
    def get_data_psb(self):
        self.login()
        self.page.goto(DATA_PSB_URL, wait_until="domcontentloaded")
        # TODO: Implement PSB data extraction
        pass


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


def get_customer_details_sync(customer_id: str, headless: bool = True) -> Optional[Customer]:
    """Get comprehensive customer details (for use with run_sync)."""
    service = CustomerService()
    try:
        service.start(headless=headless)
        return service.get_customer_details(customer_id)
    finally:
        service.close()


def get_invoice_data_sync(customer_id: str, headless: bool = True) -> Optional[CustomerwithInvoices]:
    """Get detailed invoice data for customer (for use with run_sync)."""
    service = CustomerService()
    try:
        service.start(headless=headless)
        return service.get_invoice_data(customer_id)
    finally:
        service.close()


if __name__ == "__main__":
    # Test sync version
    service = CustomerService()
    try:
        service.start(headless=False)
        
        # Fast method: use encoded customer ID directly
        # Example ID from the detail URL
        print("=== Testing FAST method (direct customer_id) ===")
        invoices = service.get_invoices(customer_id="OTEyNC0yNzAtNTA4ODIyMDU=")
        print("Invoice Data:", invoices)
        
        # Slow method: search by internet number
        # print("=== Testing SLOW method (search query) ===")
        # invoices = service.get_invoices(query="10009124")
        # print("Invoice Data:", invoices)
    finally:
        service.close()