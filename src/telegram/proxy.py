"""Proxy management for Telegram connections."""

from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

from ..core.logger import get_logger
from ..core.exceptions import ProxyError

logger = get_logger(__name__)


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    address: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    proxy_type: str = 'socks5'  # 'socks5' or 'http'

    def to_telethon_proxy(self) -> Tuple[str, str, int, bool, Optional[str], Optional[str]]:
        """
        Convert to Telethon proxy format (SOCKS5/HTTP only).

        Returns:
            Tuple: (proxy_type, addr, port, rdns, username, password)
        """
        import socks

        if self.proxy_type.lower() == 'socks5':
            proxy_type = socks.SOCKS5
        elif self.proxy_type.lower() in ['http', 'https']:
            proxy_type = socks.HTTP
        else:
            raise ProxyError(f"Unsupported proxy type: {self.proxy_type}")

        return (
            proxy_type,
            self.address,
            self.port,
            True,  # rdns
            self.username,
            self.password
        )

    def get_client_kwargs(self) -> dict:
        """
        Return kwargs for TelegramClient constructor.
        Handles all proxy types including MTProto.
        """
        if self.proxy_type.lower() == 'mtproto':
            password = self.password or ''
            # Если пришли bytes (напр. из БД как BLOB) — декодируем в str
            if isinstance(password, bytes):
                password = password.decode('utf-8')
            secret_bytes = bytes.fromhex(password)
            # dd/ee prefix → RandomizedIntermediate; plain secret → Intermediate
            if password.lower().startswith('dd') or password.lower().startswith('ee'):
                from telethon.network.connection.tcpmtproxy import ConnectionTcpMTProxyRandomizedIntermediate
                connection_cls = ConnectionTcpMTProxyRandomizedIntermediate
            else:
                from telethon.network.connection.tcpmtproxy import ConnectionTcpMTProxyIntermediate
                connection_cls = ConnectionTcpMTProxyIntermediate
            return {
                'proxy': (self.address, self.port, secret_bytes),
                'connection': connection_cls,
            }
        return {'proxy': self.to_telethon_proxy()}

    @classmethod
    def from_mtproto_link(cls, link: str) -> 'ProxyConfig':
        """
        Parse MTProto proxy link into ProxyConfig.

        Supports:
        - https://t.me/proxy?server=HOST&port=PORT&secret=SECRET
        - tg://proxy?server=HOST&port=PORT&secret=SECRET
        """
        from urllib.parse import urlparse, parse_qs

        link = link.strip()
        if 't.me/proxy' in link:
            q_idx = link.index('?')
            link = 'tg://proxy' + link[q_idx:]

        parsed = urlparse(link)
        params = parse_qs(parsed.query)

        server = params.get('server', [None])[0]
        port_str = params.get('port', [None])[0]
        secret = params.get('secret', [None])[0]

        if not server or not port_str or not secret:
            raise ValueError("Не найдены обязательные параметры: server, port, secret")
        if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
            raise ValueError("Неверный порт в ссылке")
        try:
            bytes.fromhex(secret)
        except ValueError:
            raise ValueError("Неверный формат secret (должен быть hex)")

        return cls(
            address=server,
            port=int(port_str),
            proxy_type='mtproto',
            password=secret,  # secret хранится в поле password
        )

    def __str__(self) -> str:
        """String representation."""
        auth = f"{self.username}@" if self.username else ""
        return f"{self.proxy_type}://{auth}{self.address}:{self.port}"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProxyConfig':
        """Create ProxyConfig from dictionary."""
        return cls(
            address=data['address'],
            port=data['port'],
            username=data.get('username'),
            password=data.get('password'),
            proxy_type=data.get('proxy_type', 'socks5')
        )

    def validate(self) -> bool:
        """Validate proxy configuration."""
        if not self.address:
            raise ProxyError("Proxy address is required")

        if not isinstance(self.port, int) or not (1 <= self.port <= 65535):
            raise ProxyError(f"Invalid proxy port: {self.port}")

        if self.proxy_type.lower() not in ['socks5', 'http', 'https', 'mtproto']:
            raise ProxyError(f"Unsupported proxy type: {self.proxy_type}")

        # If username is provided, password should also be provided
        if self.username and not self.password:
            logger.warning("Proxy username provided without password")

        return True


class ProxyManager:
    """Manage proxy connections."""

    def __init__(self):
        self.proxies: Dict[int, ProxyConfig] = {}

    def add_proxy(self, proxy_id: int, config: ProxyConfig) -> None:
        """Add a proxy configuration."""
        config.validate()
        self.proxies[proxy_id] = config
        logger.info(f"Прокси добавлен: {config}")

    def get_proxy(self, proxy_id: int) -> Optional[ProxyConfig]:
        """Get proxy configuration by ID."""
        return self.proxies.get(proxy_id)

    def remove_proxy(self, proxy_id: int) -> None:
        """Remove a proxy configuration."""
        if proxy_id in self.proxies:
            proxy = self.proxies.pop(proxy_id)
            logger.info(f"Прокси удален: {proxy}")

    def get_all_proxies(self) -> Dict[int, ProxyConfig]:
        """Get all proxy configurations."""
        return self.proxies.copy()
