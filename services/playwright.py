import asyncio
import logging
import os
from core.config import settings
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

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


class CustomerService:

    def __init__(self, username: str = None, password: str = None):
        self.username = username or username_cs
        self.password = password or password_cs
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.session_file = SESSION_CS_FILE

    async def start(self, headless: bool = False):
        # Ensure session directory exists
        SESSION_DIR.mkdir(exist_ok=True)
        
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=headless)
        
        # Try to load existing session
        if self.session_file.exists():
            logging.info(f"Loading session from {self.session_file}")
            self.context = await self.browser.new_context(storage_state=str(self.session_file))
        else:
            logging.info("No existing session found, creating new context")
            self.context = await self.browser.new_context()
        
        self.page = await self.context.new_page()

    async def save_session(self):
        """Save current session (cookies, localStorage) to file."""
        if self.context:
            await self.context.storage_state(path=str(self.session_file))
            logging.info(f"Session saved to {self.session_file}")

    async def close(self, save: bool = True):
        if save and self.context:
            await self.save_session()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def is_logged_in(self) -> bool:
        """Check if already logged in by navigating to dashboard."""
        try:
            await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            # If we're redirected to dashboard or can see dashboard elements, we're logged in
            await self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=3000)
            logging.info("Already logged in (session restored)")
            return True
        except PWTimeoutError:
            return False

    async def login(self) -> bool:
        if not self.page:
            raise RuntimeError("Call start() first")
        
        # Check if already logged in from saved session
        if await self.is_logged_in():
            return True

        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logging.info("Goin to Login Page")

        await self.page.get_by_placeholder("Username").fill(self.username)
        logging.info("Username filled")
        await self.page.get_by_placeholder("Password").fill(self.password)
        logging.info("Password filled")
        await self.page.get_by_role("button", name="Sign In").click()

        try:
            await self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=15_000)
            # Save session after successful login
            await self.save_session()
            return True
        except PWTimeoutError:
            # Example: check error message (adjust to the exact text on the page)
            err = self.page.get_by_text("Invalid username or password")
            if await err.is_visible():
                raise ValueError("Invalid username or password")
            # Otherwise, bubble up the real timeout
            raise

    async def search_user(self, query: str):
        ok = await self.login()
        if not ok:
            return None

        field = self.page.get_by_placeholder("Name Or No Internet")
        await field.fill(query)
        await field.press("Enter")

        # Wait for search results to load
        await self.page.wait_for_load_state("networkidle")
        await self.page.get_by_text(query, exact=False).first.wait_for(timeout=10_000)

        rows = self.page.locator("table#create_note tbody tr")
        count = await rows.count()
        logging.info(f"Found {count} rows matching '{query}'")

        data = []
        for i in range(count):
            row = rows.nth(i)
            cells = row.locator("td")
            
            # Column 1: Name and Address
            cell1 = cells.nth(0)
            name = (await cell1.locator("h5").inner_text()).strip()
            address = (await cell1.locator("small").inner_text()).strip()
            
            # Column 2: No Internet and PPPoE
            cell2 = cells.nth(1)
            smalls = cell2.locator("small")
            user_pppoe = (await smalls.nth(0).inner_text()).strip()            
            # Column 3: Status Connection (ONLINE/OFFLINE)
            cell3 = cells.nth(2)
            status_con = (await cell3.locator("span.badge").inner_text()).strip()
            
            # Column 4: Status Paket (ACTIVE/INACTIVE)
            cell4 = cells.nth(3)
            status_pkt = (await cell4.locator("span.badge").inner_text()).strip()
            
            # Column 5: Get customer ID from detail link (href contains id=XXXX)
            cell5 = cells.nth(4)
            detail_link = cell5.locator("a[href*='deusr']")
            href = await detail_link.get_attribute("href")
            # Extract id from href like "index.php?csp=deusr&id=7341"
            id_pelanggan = href.split("id=")[-1] if href else ""
            
            # Extract maps (coordinat) and mobile (no_hp) from hidden inputs in the note modal
            # The modal has ID like #create_note_modal7341
            note_modal = self.page.locator(f"#create_note_modal{id_pelanggan}")
            
            # Get coordinat value for maps
            coordinat_input = note_modal.locator("input[name='coordinat']").first
            maps = ""
            if await coordinat_input.count() > 0:
                maps = await coordinat_input.get_attribute("value") or ""
            
            # Get no_hp value for mobile
            no_hp_input = note_modal.locator("input[name='no_hp']")
            mobile = ""
            if await no_hp_input.count() > 0:
                mobile = await no_hp_input.get_attribute("value") or ""
            
            # Get package from the migration modal (paket_lama)
            migration_modal = self.page.locator(f"#create_timi_pa{id_pelanggan}")
            paket_input = migration_modal.locator("input[name='paket_lama']")
            package = ""
            if await paket_input.count() > 0:
                package = await paket_input.get_attribute("value") or ""

            data.append({
                "id": id_pelanggan,
                "name": name,
                "address": address,
                "user_pppoe": user_pppoe,
                "package": package,
                "status_con": status_con,
                "status_pkt": status_pkt,
                "mobile": mobile,
                "maps": maps
            })
        logging.info(data)

        return data
    
    async def get_invoices(self, id_pelanggan: str):
        """Get invoice data for a customer by their ID (not search keyword).
        Requires login() to have been called first.
        """
        invoices_url = INVOICES_URL.format(id=id_pelanggan)
        logging.info(f"Goin to {invoices_url}")
        await self.page.goto(invoices_url, wait_until="networkidle")
        
        # Get status (PAID/UNPAID) from buttons
        # PAID button has btn-success class, UNPAID button has btn-danger class
        paid_button = self.page.locator("button.btn.btn-success.btn-xs:has-text('PAID')")
        unpaid_button = self.page.locator("button.btn.btn-danger.btn-xs:has-text('UNPAID')")
        
        status = "UNKNOWN"
        if await paid_button.count() > 0:
            status = "PAID"
        elif await unpaid_button.count() > 0:
            status = "UNPAID"
        
        logging.info(f"Payment status: {status}")
        
        # Get ticket info from the Ticket Info table
        # The table is under h5 with "Ticket Info" text
        ticket_rows = self.page.locator("#timeline table tbody tr")
        ticket_count = await ticket_rows.count()
        logging.info(f"Found {ticket_count} ticket rows")
        
        tickets = []
        for i in range(ticket_count):
            row = ticket_rows.nth(i)
            # Get ticket reference (TN016361 format)
            ref = (await row.locator("td").nth(0).locator("h6").inner_text()).strip()
            # Get date
            date = (await row.locator("td").nth(1).locator("h6").inner_text()).strip()
            # Get details
            details = (await row.locator("td").nth(2).locator("h6").inner_text()).strip()
            
            tickets.append({
                "ref": ref,
                "date": date,
                "details": details
            })
        
        
        logging.info(f"Tickets: {tickets}")
        
        # Get invoice description from the textarea in the modal
        # Contains full billing message with customer name, amount, payment link, etc.
        invoice_desc = self.page.locator("textarea.form-control[name='deskripsi_edit']")
        invoice_text = ""
        if await invoice_desc.count() > 0:
            invoice_text = await invoice_desc.first.input_value()
        
        logging.info(f"Invoice description: {invoice_text}")
        
        result = {
            "status": status,
            "tickets": tickets,
            "invoice_description": invoice_text
        }
        
        logging.info(f"Invoice data: {result}")
        
        return result
    
    async def create_ticket(self, id_pelanggan: str, description: str):
        """Create a ticket for a customer. Requires search_user to have been called first
        to be on the search results page where the modal can be opened.
        
        Args:
            id_pelanggan: Customer ID (not search keyword)
            description: Description text for the ticket
        """
        # First search for the customer to get to the page with the modals
        await self.search_user(id_pelanggan)
        
        # Click the action dropdown for this customer's row
        # Find the row with the matching customer ID and click its dropdown
        action_dropdown = self.page.locator(f"a[data-target='#create_tiga_modal{id_pelanggan}']")
        
        if await action_dropdown.count() == 0:
            logging.error(f"Could not find Ticket Gangguan link for customer {id_pelanggan}")
            return None
        
        # Click to open the "Ticket Gangguan" modal
        await action_dropdown.click()
        logging.info(f"Opened Ticket Gangguan modal for customer {id_pelanggan}")
        
        # Wait for modal to be visible
        modal = self.page.locator(f"#create_tiga_modal{id_pelanggan}")
        await modal.wait_for(state="visible", timeout=5000)
        
        # Select Priority = LOW
        priority_select = modal.locator("select[name='priority']")
        await priority_select.select_option("LOW")
        logging.info("Selected priority: LOW")
        
        # Select Type = FREE
        type_select = modal.locator("select[name='jenis_ticket']")
        await type_select.select_option("FREE")
        logging.info("Selected type: FREE")
        
        # Fill description
        description_textarea = modal.locator("textarea[name='deskripsi']")
        await description_textarea.fill(description)
        logging.info(f"Filled description: {description}")
        
        # Click Save button
        save_button = modal.locator("button[name='create_ticket_gangguan']")
        await save_button.click()
        logging.info("Clicked Save button")
        
        # Wait for page to process the form
        await self.page.wait_for_load_state("networkidle")
        
        logging.info(f"Ticket created successfully for customer {id_pelanggan}")
        return True


class NOC:
    def __init__(self, username: str = None, password: str = None):
        self.username = username or username_noc
        self.password = password or password_noc
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.session_file = SESSION_NOC_FILE

    async def start(self, headless: bool = True):
        # Ensure session directory exists
        SESSION_DIR.mkdir(exist_ok=True)
        
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=headless)
        
        # Try to load existing session
        if self.session_file.exists():
            logging.info(f"Loading NOC session from {self.session_file}")
            self.context = await self.browser.new_context(storage_state=str(self.session_file))
        else:
            logging.info("No existing NOC session found, creating new context")
            self.context = await self.browser.new_context()
        
        self.page = await self.context.new_page()

    async def save_session(self):
        """Save current session (cookies, localStorage) to file."""
        if self.context:
            await self.context.storage_state(path=str(self.session_file))
            logging.info(f"NOC session saved to {self.session_file}")

    async def close(self, save: bool = True):
        if save and self.context:
            await self.save_session()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def is_logged_in(self) -> bool:
        """Check if already logged in by navigating to dashboard."""
        try:
            await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=3000)
            logging.info("Already logged in (NOC session restored)")
            return True
        except PWTimeoutError:
            return False

    async def login(self) -> bool:
        if not self.page:
            raise RuntimeError("Call start() first")
        
        # Check if already logged in from saved session
        if await self.is_logged_in():
            return True

        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        logging.info("NOC: Going to Login Page")

        await self.page.get_by_placeholder("Username").fill(self.username)
        logging.info("NOC: Username filled")
        await self.page.get_by_placeholder("Password").fill(self.password)
        logging.info("NOC: Password filled")
        await self.page.get_by_role("button", name="Sign In").click()

        try:
            await self.page.wait_for_url(DASHBOARD_URL_GLOB, timeout=15_000)
            # Save session after successful login
            await self.save_session()
            return True
        except PWTimeoutError:
            err = self.page.get_by_text("Invalid username or password")
            if await err.is_visible():
                raise ValueError("Invalid username or password")
            raise
    
    async def process_ticket(self, nama_pelanggan: str, action: str):
        """Process a ticket with the given action and notes.
        
        Args:
            nama_pelanggan: name of the customer
            action: send the "cek"
        """

        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        # TODO: Implement ticket processing logic
        pass
    
    async def get_data_psb(self):
        """Get data PSB from the page."""
        await self.login()
        await self.page.goto(DATA_PSB_URL, wait_until="domcontentloaded")

        psb_data = await self.page.locator("table").all_rows()

async def main():
    t = CustomerService()
    try:
        await t.start(headless=False)
        data = await t.search_user("10007295")
        print(data)
        
        # Get invoices for the first customer found (using their actual ID)
        if data and len(data) > 0:
            first_customer_id = data[0]["id"]
            data_invoices = await t.get_invoices(first_customer_id)
            print(data_invoices)

    finally:
        await t.close()


if __name__ == "__main__":
    asyncio.run(main())