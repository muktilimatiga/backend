import logging
from core.config import settings
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from concurrent.futures import ThreadPoolExecutor
import asyncio

# Configure logging to show messages
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

LOGIN_URL = settings.LOGIN_URL_BILLING
DASHBOARD_URL_GLOB = "**/billing2/**/index.php"  # more flexible than exact URL
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

        rows = self.page.locator("table#create_note tbody tr")
        count = rows.count()
        logging.info(f"Found {count} rows matching '{query}'")

        data = []
        for i in range(count):
            row = rows.nth(i)
            cells = row.locator("td")
            
            # Column 1: Name and Address
            cell1 = cells.nth(0)
            name = cell1.locator("h5").inner_text().strip()
            address = cell1.locator("small").inner_text().strip()
            
            # Column 2: No Internet and PPPoE
            cell2 = cells.nth(1)
            smalls = cell2.locator("small")
            user_pppoe = smalls.nth(0).inner_text().strip()
            
            # Column 3: Status Connection (ONLINE/OFFLINE)
            cell3 = cells.nth(2)
            status_con = cell3.locator("span.badge").inner_text().strip()
            
            # Column 4: Status Paket (ACTIVE/INACTIVE)
            cell4 = cells.nth(3)
            status_pkt = cell4.locator("span.badge").inner_text().strip()
            
            # Column 5: Get customer ID from detail link
            cell5 = cells.nth(4)
            detail_link = cell5.locator("a[href*='deusr']")
            href = detail_link.get_attribute("href")
            id_pelanggan = href.split("id=")[-1] if href else ""
            
            # Extract maps (coordinat) and mobile (no_hp) from hidden inputs
            note_modal = self.page.locator(f"#create_note_modal{id_pelanggan}")
            
            coordinat_input = note_modal.locator("input[name='coordinat']").first
            maps = ""
            if coordinat_input.count() > 0:
                maps = coordinat_input.get_attribute("value") or ""
            
            no_hp_input = note_modal.locator("input[name='no_hp']")
            mobile = ""
            if no_hp_input.count() > 0:
                mobile = no_hp_input.get_attribute("value") or ""
            
            # Get package from migration modal
            migration_modal = self.page.locator(f"#create_timi_pa{id_pelanggan}")
            paket_input = migration_modal.locator("input[name='paket_lama']")
            package = ""
            if paket_input.count() > 0:
                package = paket_input.get_attribute("value") or ""

            data.append({
                "id": id_pelanggan,
                "nama": name,
                "alamat": address,
                "user_pppoe": user_pppoe,
                "paket": package,
                "status": status_con,
                "status_paket": status_pkt,
                "mobile": mobile,
                "maps": maps
            })
        logging.info(data)

        return data
    
    def get_invoices(self, id_pelanggan: str):
        """Get invoice data for a customer by their ID."""
        invoices_url = INVOICES_URL.format(id=id_pelanggan)
        logging.info(f"Going to {invoices_url}")
        self.page.goto(invoices_url, wait_until="networkidle")
        
        # Get status (PAID/UNPAID) from buttons
        paid_button = self.page.locator("button.btn.btn-success.btn-xs:has-text('PAID')")
        unpaid_button = self.page.locator("button.btn.btn-danger.btn-xs:has-text('UNPAID')")
        
        status = "UNKNOWN"
        try:
            if paid_button.count() > 0:
                status = "PAID"
            elif unpaid_button.count() > 0:
                status = "UNPAID"
        except Exception as e:
            logging.warning(f"Could not determine payment status: {e}")
        
        logging.info(f"Payment status: {status}")
        
        # Get ticket info with error handling
        tickets = []
        try:
            ticket_table = self.page.locator("#timeline table tbody")
            # Check if table exists first with short timeout
            if ticket_table.count() > 0:
                ticket_rows = ticket_table.locator("tr")
                ticket_count = ticket_rows.count()
                logging.info(f"Found {ticket_count} ticket rows")
                
                for i in range(ticket_count):
                    try:
                        row = ticket_rows.nth(i)
                        cells = row.locator("td")
                        
                        # Try to get text with short timeout
                        ref = ""
                        date = ""
                        details = ""
                        
                        try:
                            h6_ref = cells.nth(0).locator("h6")
                            if h6_ref.count() > 0:
                                ref = h6_ref.inner_text(timeout=2000).strip()
                        except:
                            pass
                        
                        try:
                            h6_date = cells.nth(1).locator("h6")
                            if h6_date.count() > 0:
                                date = h6_date.inner_text(timeout=2000).strip()
                        except:
                            pass
                        
                        try:
                            h6_details = cells.nth(2).locator("h6")
                            if h6_details.count() > 0:
                                details = h6_details.inner_text(timeout=2000).strip()
                        except:
                            pass
                        
                        if ref or date or details:
                            tickets.append({
                                "ref_id": ref,
                                "date_created": date,
                                "description": details
                            })
                    except Exception as row_error:
                        logging.warning(f"Error parsing ticket row {i}: {row_error}")
                        continue
            else:
                logging.info("No ticket table found")
        except Exception as e:
            logging.warning(f"Could not fetch tickets: {e}")
        
        logging.info(f"Tickets: {tickets}")
        
        # Get invoice description with error handling
        invoice_text = ""
        try:
            invoice_desc = self.page.locator("textarea.form-control[name='deskripsi_edit']")
            if invoice_desc.count() > 0:
                invoice_text = invoice_desc.first.input_value(timeout=5000)
        except Exception as e:
            logging.warning(f"Could not fetch invoice description: {e}")
        
        logging.info(f"Invoice description: {invoice_text[:100] if invoice_text else 'empty'}...")
        
        return {
            "status": status,
            "tickets": tickets,
            "invoice_description": invoice_text
        }
    
    def create_ticket(self, id_pelanggan: str, description: str):
        """Create a ticket for a customer."""
        self.search_user(id_pelanggan)
        
        action_dropdown = self.page.locator(f"a[data-target='#create_tiga_modal{id_pelanggan}']")
        
        if action_dropdown.count() == 0:
            logging.error(f"Could not find Ticket Gangguan link for customer {id_pelanggan}")
            return None
        
        action_dropdown.click()
        logging.info(f"Opened Ticket Gangguan modal for customer {id_pelanggan}")
        
        modal = self.page.locator(f"#create_tiga_modal{id_pelanggan}")
        modal.wait_for(state="visible", timeout=5000)
        
        modal.locator("select[name='priority']").select_option("LOW")
        logging.info("Selected priority: LOW")
        
        modal.locator("select[name='jenis_ticket']").select_option("FREE")
        logging.info("Selected type: FREE")
        
        modal.locator("textarea[name='deskripsi']").fill(description)
        logging.info(f"Filled description: {description}")
        
        modal.locator("button[name='create_ticket_gangguan']").click()
        logging.info("Clicked Save button")
        
        self.page.wait_for_load_state("networkidle")
        
        logging.info(f"Ticket created successfully for customer {id_pelanggan}")
        return True


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


if __name__ == "__main__":
    # Test sync version
    service = CustomerService()
    try:
        service.start(headless=False)
        data = service.search_user("10007295")
        print(data)
        
        if data and len(data) > 0:
            invoices = service.get_invoices(data[0]["id"])
            print(invoices)
    finally:
        service.close()