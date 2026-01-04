import asyncio
import logging
import re
import telnetlib3
from core.olt_config import COMMAND_TEMPLATES


logging.basicConfig(level=logging.INFO)
logging.getLogger("telnetlib3").setLevel(logging.ERROR)

class SwitchClient:
    def __init__(self, host: str, username: str, password: str, is_huawei: bool):
        self.host = host
        self.username = username
        self.password = password
        self.is_huawei = is_huawei
        self._lock = None
        self.reader = None
        self.writer = None
        self.last_activity = 0
        self._prompt_re = re.compile(r"(.+[>#])\s*$")
        self._pagination_prompt = "---- More ----"
    
    @property
    def lock(self):
        try:
            current_loop = asyncio.get_running_loop()
            if self._lock is None or self._lock._loop is not current_loop:
                self._lock = asyncio.Lock()
        except RuntimeError:
            if self._lock is None:
                self._lock = asyncio.Lock()
        return self._lock

    def _get_action_command(self, action: str, **kwargs) -> list[str]:
        """Action untuk menentukan devices huawei atau cisco"""
        device = "huawei" if self.is_huawei else "cisco"
        template = COMMAND_TEMPLATES.get(action, {}).get(device, [])
        return [cmd.format(**kwargs) for cmd in template]

    async def connect(self):
        """Connect ke device"""
        if self.writer and not self.writer.is_closing():
            return  # Already connected

        logging.info(f"Membuka koneksi baru ke {self.host}...")
        self.reader, self.writer = await asyncio.wait_for(
            telnetlib3.open_connection(self.host, 23), timeout=20
        )
        await self._login()
        self.last_activity = asyncio.get_event_loop().time()
    
    async def close(self):
        """Close Manual"""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except:
                pass
        self.writer = None
        self.reader = None

    async def _read_until_prompt(self, timeout: int = 20) -> str:
        """Check prompt yang keluar dari devices setelah login"""
        if not self.reader:
            raise ConnectionError("Telnet reader tidak tersedia.")
        try:
            data = ""
            while True:
                chunk = await asyncio.wait_for(self.reader.read(1024), timeout=timeout)
                if not chunk:
                    break
                data += chunk

                if re.search(self._prompt_re, data):
                    break
                    
                if self._pagination_prompt in data:
                    if not self.writer:
                        raise ConnectionError("Writer closed during pagination")
                    self.writer.write(" ")
                    await self.writer.drain()
                    data = data.replace(self._pagination_prompt, "")
            return data
        except asyncio.TimeoutError:
            logging.warning(f"Timeout setelah menunggu prompt dari {self.host}")
            raise
        except Exception as e:
            raise ConnectionError(f"Error membaca output dari device {self.host}: {e}")
    
    async def _login(self, timeout: int = 20):
        """
        Login untuk device cisco dan huawei.
        Huawei: first prompt is 'Login:'
        Cisco: first prompt is 'Username:'
        """
        try:
            # Different login prompt based on device type
            if self.is_huawei:
                login_prompt = b'Login:'
            else:
                login_prompt = b'Username:'
            
            # Wait for login/username prompt
            await asyncio.wait_for(self.reader.readuntil(login_prompt), timeout=timeout)
            self.writer.write(self.username + '\n')
            
            # Wait for password prompt
            await asyncio.wait_for(self.reader.readuntil(b'Password:'), timeout=timeout)
            self.writer.write(self.password + '\n')

            # Wait for the main prompt after login
            await self._read_until_prompt(timeout=timeout)
            
            logging.info(f"Successfully logged in to device {self.host}")
            
        except asyncio.TimeoutError:
            await self.close()
            raise ConnectionError(f"Timeout during login to {self.host}")
        except Exception as e:
            await self.close()
            raise ConnectionError(f"Failed to login: {e}")

    async def _execute_command(self, command: str, timeout: int = 20) -> str:
        """Execute command on device"""
        if not self.reader or not self.writer:
            raise ConnectionError("Connection not established to execute command.")
        if not command:
            return ""
        
        self.writer.write(command + "\n")
        await asyncio.wait_for(self.writer.drain(), timeout=10)
        raw_output = await self._read_until_prompt(timeout=timeout)
        
        # Clean output (remove command echo and prompt)
        cleaned_lines = []
        lines = raw_output.splitlines()
        if len(lines) > 2:
            for line in lines[1:-1]:
                stripped = line.strip()
                if stripped:
                    cleaned_lines.append(stripped)
        
        return "\n".join(cleaned_lines)